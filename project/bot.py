from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

# --- NEW IMPORTS ---
import phonenumbers
from phonenumbers import geocoder
# -------------------

from telethon import Button, TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import KeyboardButton, KeyboardButtonRow, ReplyKeyboardMarkup

from admin import AdminState, handle_admin_callback, handle_admin_message, render_admin_panel
from config import load_config
from database import Database
from session_pool import RoundRobinPool
from stock_manager import StockManager

@dataclass
class UserState:
    waiting_for: str | None = None  # "deposit_upi_amount" | "deposit_upi_utr" | "deposit_usdt" | "support"
    deposit_method: str | None = None
    deposit_amount: int | None = None

class RateLimiter:
    def __init__(self, min_interval_sec: float) -> None:
        self._min_interval = float(min_interval_sec)
        self._last: dict[int, float] = {}

    def allow(self, user_id: int) -> bool:
        now = time.time()
        last = self._last.get(user_id, 0.0)
        if now - last < self._min_interval:
            return False
        self._last[user_id] = now
        return True

def user_menu() -> list[list[Button]]:
    return [
        [Button.inline("👤 Account", b"u:account"), Button.inline("📜 History", b"u:tx")],
        [Button.inline("📞 Last number", b"u:last"), Button.inline("🛒 Buy number", b"u:buy")],
        [Button.inline("💰 Deposit", b"u:deposit"), Button.inline("🛠️ Support", b"u:support")],
    ]

# --- Main user menu (updated) ---
def user_menu() -> list[list[Button]]:
    return [
        [Button.inline("\U0001F464 Account", b"u:account"), Button.inline("\U0001F4DC Transactions", b"u:tx")],
        [Button.inline("\U0001F6D2 Buy", b"u:buy"), Button.inline("\U0001F381 Refer & Earn", b"u:refer")],
        [Button.inline("\U0001F4B0 Deposit", b"u:deposit"), Button.inline("\U0001F6E0 Support", b"u:support")],
    ]

BTN_ACCOUNT = "\U0001F464 Account"
BTN_TX = "\U0001F4DC Transactions"
BTN_BUY = "\U0001F6D2 Buy"
BTN_REFER = "\U0001F381 Refer & Earn"
BTN_DEPOSIT = "\U0001F4B0 Deposit"
BTN_SUPPORT = "\U0001F6E0 Support"

BTN_CONFIRM_BUY = "\u2705 Confirm & Buy"
BTN_CANCEL = "\u274C Cancel"
BTN_BACK_MENU = "\U0001F519 Menu"
BTN_BACK_DEPOSIT = "\U0001F519 Deposit"

BTN_UPI = "\U0001F4B3 UPI"
BTN_USDT = "\U0001FA99 USDT"
BTN_SUBMIT_UTR = "\u2705 Submit UTR"

def _reply_kb(rows: list[list[str]], *, resize: bool = True) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        rows=[KeyboardButtonRow([KeyboardButton(text=t) for t in row]) for row in rows],
        resize=resize,
        single_use=False,
        selective=False,
    )

def user_menu() -> ReplyKeyboardMarkup:
    return _reply_kb(
        [
            [BTN_ACCOUNT, BTN_TX],
            [BTN_BUY, BTN_REFER],
            [BTN_DEPOSIT, BTN_SUPPORT],
        ]
    )

def _buy_confirm_kb() -> ReplyKeyboardMarkup:
    return _reply_kb([[BTN_CONFIRM_BUY, BTN_CANCEL], [BTN_BACK_MENU]])

def _deposit_methods_kb() -> ReplyKeyboardMarkup:
    return _reply_kb([[BTN_UPI, BTN_USDT], [BTN_BACK_MENU]])

def _upi_kb() -> ReplyKeyboardMarkup:
    return _reply_kb([[BTN_SUBMIT_UTR], [BTN_BACK_DEPOSIT, BTN_BACK_MENU]])

# --- UPGRADED: Automatic Country Detection ---
def get_country_info(phone: str) -> str:
    try:
        # Session filename se ".session" hatayein agar ho toh
        clean_phone = phone.replace(".session", "")
        if not clean_phone.startswith("+"):
            clean_phone = "+" + clean_phone
            
        parsed = phonenumbers.parse(clean_phone, None)
        # Country name (English)
        country_name = geocoder.description_for_number(parsed, "en")
        # Region code (e.g. IN, US) for Flag Emoji
        region_code = phonenumbers.region_code_for_number(parsed)
        
        if region_code:
            # Unicode magic to get Flag Emoji
            flag = "".join(chr(127397 + ord(c)) for c in region_code)
            return f"{flag} {country_name}"
        
        return "🌍 International"
    except:
        return "🌍 International"

DEFAULT_START_TEXT = "Welcome!\n\nUse the menu below to view your account, deposit funds, or buy items from stock."
DEFAULT_DASHBOARD_TEXT = "Main dashboard"
DEFAULT_DEPOSIT_TEXT = "\U0001F4B0 Deposit"
DEFAULT_SUPPORT_TEXT = "Support\n\nSend your message. Admin will reply."

async def main() -> None:
    cfg = load_config()
    Path(cfg.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.sessions_dir).mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("bot")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(os.path.join(cfg.logs_dir, "bot.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)

    db = Database(cfg.db_path)
    await db.init()

    stock = StockManager(db, cfg.api_id, cfg.api_hash)
    pool = RoundRobinPool(cfg.sessions_dir, max_items=100, cooldown_sec=20)
    pool.load()

    user_state: dict[int, UserState] = {}
    admin_state: dict[int, AdminState] = {}
    limiter = RateLimiter(cfg.user_rate_limit_per_sec)

    # Use a token-derived session name so switching bots doesn't reuse old sessions.
    bot_id = cfg.bot_token.split(":", 1)[0]
    bot_session_name = os.path.join(cfg.logs_dir, f"bot_client_{bot_id}")
    client = TelegramClient(bot_session_name, cfg.api_id, cfg.api_hash)
    await client.start(bot_token=cfg.bot_token)
    me = await client.get_me()
    if not getattr(me, "bot", False):
        session_file = f"{bot_session_name}.session"
        ident = getattr(me, "username", None) or getattr(me, "id", None) or "unknown"
        logger.error(
            "Bot did not log in (session is authorized as user: %s). Delete %s and restart.",
            ident,
            session_file,
        )
        await client.disconnect()
        return
    bot_username = getattr(me, "username", None) or ""

    async def safe_send(to_id: int, text: str, *, buttons=None, parse_mode: str | None = None) -> None:
        try:
            await client.send_message(to_id, text, buttons=buttons, parse_mode=parse_mode)
        except FloodWaitError as e:
            await asyncio.sleep(int(e.seconds) + 1)
            await client.send_message(to_id, text, buttons=buttons, parse_mode=parse_mode)

    async def safe_send_file(to_id: int, file_path: str, *, caption: str, buttons=None) -> None:
        try:
            await client.send_file(to_id, file_path, caption=caption, buttons=buttons, parse_mode="md")
        except FloodWaitError as e:
            await asyncio.sleep(int(e.seconds) + 1)
            await client.send_file(to_id, file_path, caption=caption, buttons=buttons, parse_mode="md")

    async def ensure_user(event: events.NewMessage.Event) -> bool:
        sender = await event.get_sender()
        await db.upsert_user(event.sender_id, getattr(sender, "username", None), getattr(sender, "first_name", None))
        u = await db.get_user(event.sender_id)
        if u and u.is_banned:
            await event.respond("You are banned.")
            return False
        return True

    async def send_page(chat_id: int, text: str, image_path: str | None = None, buttons=None) -> None:
        if image_path and os.path.exists(image_path):
            await client.send_file(chat_id, image_path, caption=text, buttons=buttons, parse_mode="md")
            return
        await client.send_message(chat_id, text, buttons=buttons, parse_mode="md")

    async def get_ui_text(key: str, default: str) -> str:
        val = await db.get_setting(key)
        return val if val and val.strip() else default

    async def get_ui_image(key: str) -> str | None:
        val = await db.get_setting(key)
        return val if val and val.strip() else None

    async def _maybe_answer(ev, *args, **kwargs) -> None:
        fn = getattr(ev, "answer", None)
        if callable(fn):
            try:
                await fn(*args, **kwargs)
            except Exception:
                return

    async def handle_user_action(event, action: str) -> None:
        if not limiter.allow(event.sender_id):
            await _maybe_answer(event, "Slow down", alert=False)
            return

        u = await db.get_user(event.sender_id)
        if u and u.is_banned:
            await _maybe_answer(event, "Banned", alert=True)
            return

        if action == "u:account":
            if not u:
                await event.respond("Use /start first.", buttons=user_menu())
                await _maybe_answer(event)
                return
            dash_text = await get_ui_text("dashboard_text", DEFAULT_DASHBOARD_TEXT)
            dash_img = await get_ui_image("dashboard_image_path")
            text = f"{dash_text}\n\n\U0001F464 **Account Info:**\nID: `{u.tg_id}`\nBalance: `{u.balance} INR`"
            await send_page(event.chat_id, text, image_path=dash_img, buttons=user_menu())
            await _maybe_answer(event)
            return

        if action == "u:buy":
            if not u:
                await event.respond("Use /start first.", buttons=user_menu())
                await _maybe_answer(event)
                return
            info = await db.get_next_stock_info()
            if not info:
                await event.respond("\u274C Out of stock. Please try later.", buttons=user_menu())
                await _maybe_answer(event)
                return

            country = get_country_info(info["example"])
            text = (
                "\U0001F6D2 **Confirm Purchase**\n\n"
                f"\U0001F30D Country: **{country}**\n"
                f"\U0001F4E6 Total Stock: `{info['total']}`\n"
                f"\U0001F4B0 Price: `{info['price']} INR`\n\n"
                f"\U0001F4B3 Your Balance: `{u.balance} INR`"
            )
            await event.respond(text, parse_mode="md", buttons=_buy_confirm_kb())
            await _maybe_answer(event)
            return

        if action == "u:confirm_buy":
            res = await stock.buy_item(event.sender_id)
            if not res.ok:
                await event.respond(res.message, buttons=user_menu())
                await _maybe_answer(event)
                return

            phone_display = res.item.replace(".session", "")
            country_info = get_country_info(phone_display)
            await event.respond(
                "\u2705 **Purchase Successful!**\n\n"
                f"\U0001F4DE **Number:** `{phone_display}`\n"
                f"\U0001F30D **Country:** {country_info}\n"
                f"\U0001F4B0 Price: `{res.price} INR`\n\n"
                "\u231B **Bot is now monitoring for OTP...**\n"
                "Please login. Code will appear here.",
                buttons=user_menu(),
            )

            session_path = Path(cfg.sessions_dir) / res.item
            asyncio.create_task(
                stock.start_otp_listener(
                    session_path=session_path,
                    user_id=event.sender_id,
                    stock_id=res.stock_id,
                    bot_client=client,
                )
            )
            await _maybe_answer(event)
            return

        if action == "u:tx":
            txs = await db.get_transactions(event.sender_id, limit=10)
            if not txs:
                await event.respond("No transactions yet.", buttons=user_menu())
                await _maybe_answer(event)
                return
            lines = ["Last transactions (latest first):"]
            for t in txs:
                lines.append(
                    f"#{t.get('id')} | `{t.get('type')}` | `{t.get('amount')}` | `{t.get('created_at')}`\n{(t.get('description') or '').strip()}"
                )
            await event.respond("\n\n".join(lines), parse_mode="md", buttons=user_menu())
            await _maybe_answer(event)
            return

        if action == "u:refer":
            u2 = await db.get_user(event.sender_id)
            if not u2:
                await event.respond("Use /start first.", buttons=user_menu())
                await _maybe_answer(event)
                return
            bonus_raw = await db.get_setting("referral_bonus")
            try:
                bonus = int(bonus_raw) if bonus_raw else 0
            except Exception:
                bonus = 0
            total = await db.referrals_count(event.sender_id)
            referred_by = u2.referred_by
            link = f"https://t.me/{bot_username}?start=ref_{event.sender_id}" if bot_username else f"/start ref_{event.sender_id}"
            text = (
                "Refer & Earn\n\n"
                f"Your referral link:\n`{link}`\n\n"
                f"Bonus per referral: `{bonus}`\n"
                f"Total referrals: `{total}`\n"
                f"Referred by: `{referred_by if referred_by else '-'}`"
            )
            await event.respond(text, parse_mode="md", buttons=user_menu())
            await _maybe_answer(event)
            return

        if action == "u:last":
            last = await db.last_purchase(event.sender_id)
            if not last:
                await event.respond("No history found.", buttons=user_menu())
            else:
                phone_display = last["item"].replace(".session", "")
                country_info = get_country_info(phone_display)
                otp_info = f"\n\U0001F511 **OTP:** `{last.get('otp_code')}`" if last.get("otp_code") else ""
                await event.respond(
                    f"\U0001F4DE **Last purchase:**\nNumber: `{phone_display}`\nCountry: {country_info}{otp_info}",
                    buttons=user_menu(),
                )
            await _maybe_answer(event)
            return

        if action == "u:deposit":
            text = await get_ui_text("deposit_text", DEFAULT_DEPOSIT_TEXT)
            upi_id = await db.get_setting("deposit_upi_id") or "-"
            usdt_wallet = await db.get_setting("deposit_usdt_wallet") or "-"
            deposit_note = await db.get_setting("deposit_note") or ""
            full = (
                f"{text}\n\n"
                "\u26A0\uFE0F Deposit manually add by admin.\n\n"
                f"{BTN_UPI} ID: `{upi_id}`\n"
                f"{BTN_USDT} Wallet: `{usdt_wallet}`\n\n"
                f"{deposit_note}\n\n"
                "Choose a method from the keyboard:"
            )
            await event.respond(full, parse_mode="md", buttons=_deposit_methods_kb())
            await _maybe_answer(event)
            return

        if action == "u:support":
            user_state[event.sender_id] = UserState(waiting_for="support")
            text = await get_ui_text("support_text", DEFAULT_SUPPORT_TEXT)
            await event.respond(text, buttons=user_menu())
            await _maybe_answer(event)
            return

        if action == "u:dep_upi":
            upi_id = await db.get_setting("deposit_upi_id") or "-"
            upi_qr = await db.get_setting("deposit_upi_qr_path")
            note = await db.get_setting("deposit_note") or ""
            caption = (
                "\U0001F4B3 UPI Deposit\n\n"
                f"UPI ID: `{upi_id}`\n\n"
                f"{note}\n\n"
                "Jitna amount bhejna hai bhej do.\n"
                f"Payment ke baad **{BTN_SUBMIT_UTR}** dabao."
            )
            await send_page(event.chat_id, caption, image_path=upi_qr, buttons=_upi_kb())
            await _maybe_answer(event)
            return

        if action == "u:dep_upi_submit":
            user_state[event.sender_id] = UserState(waiting_for="deposit_upi_amount", deposit_method="UPI", deposit_amount=None)
            await event.respond("\U0001F4B5 Enter amount (number only). Example: `100`", parse_mode="md", buttons=user_menu())
            await _maybe_answer(event)
            return

        if action == "u:dep_usdt":
            user_state[event.sender_id] = UserState(waiting_for="deposit_usdt", deposit_method="USDT")
            wallet = await db.get_setting("deposit_usdt_wallet") or "-"
            caption = "\U0001FA99 USDT Deposit\n\n" f"Wallet: `{wallet}`\n\n" "Now send:\n`amount txid`"
            await event.respond(caption, parse_mode="md", buttons=user_menu())
            await _maybe_answer(event)
            return

        await _maybe_answer(event)

    @client.on(events.NewMessage(pattern=r"^/start(?:\s+(.*))?$"))
    async def on_start(event: events.NewMessage.Event) -> None:
        if not limiter.allow(event.sender_id): return
        if not await ensure_user(event): return

        arg = (event.pattern_match.group(1) or "").strip()
        if arg.startswith("ref_"):
            ref_raw = arg.replace("ref_", "", 1).strip()
            if ref_raw.isdigit():
                referrer_id = int(ref_raw)
                bonus_raw = await db.get_setting("referral_bonus")
                try:
                    bonus = int(bonus_raw) if bonus_raw else 0
                except Exception:
                    bonus = 0
                try:
                    await db.upsert_user(referrer_id, None, None)
                    res = await db.referral_apply(event.sender_id, referrer_id, bonus_amount=0)
                    if res.get("ok"):
                        sender = await event.get_sender()
                        uname = getattr(sender, "username", None)
                        display = f"@{uname}" if uname else (getattr(sender, "first_name", None) or str(event.sender_id))
                        await safe_send(
                            referrer_id,
                            f"🎉 New referral joined via your link:\nUser: {display}\nID: {event.sender_id}\n\nBonus will be credited after their first approved deposit.",
                        )
                except Exception:
                    logger.exception("Referral apply failed")
        text = await get_ui_text("start_text", DEFAULT_START_TEXT)
        img = await get_ui_image("start_image_path")
        await send_page(event.chat_id, text, image_path=img, buttons=user_menu())

    @client.on(events.NewMessage(pattern=r"^/admin$"))
    async def on_admin(event: events.NewMessage.Event) -> None:
        if event.sender_id not in cfg.admin_ids: return
        await render_admin_panel(event, db)

    @client.on(events.CallbackQuery)
    async def on_callback(event: events.CallbackQuery.Event) -> None:
        try:
            data = (event.data or b"").decode("utf-8", "ignore")

            # Deposit request decisions (admin)
            if data.startswith("a:dep_accept:") or data.startswith("a:dep_decline:"):
                if event.sender_id not in cfg.admin_ids:
                    await event.answer("Not allowed", alert=True)
                    return
                parts = data.split(":")
                if len(parts) != 3 or not parts[2].isdigit():
                    await event.answer("Bad request", alert=True)
                    return
                req_id = int(parts[2])
                approve = parts[1] == "dep_accept"
                decision = await db.decide_deposit_request(req_id, decided_by=event.sender_id, approve=approve)
                if not decision.get("ok"):
                    await event.answer(f"Failed: {decision.get('reason')}", alert=True)
                    return
                req = decision["request"]
                user_id = int(req["tg_id"])
                if approve:
                    await safe_send(user_id, f"✅ Deposit approved.\nAmount: {req['amount']}\nMethod: {req['method']}\nRequest ID: #{req_id}")
                else:
                    await safe_send(user_id, f"❌ Deposit declined.\nAmount: {req['amount']}\nMethod: {req['method']}\nRequest ID: #{req_id}")
                reward = decision.get("referral_reward")
                if reward and isinstance(reward, dict):
                    try:
                        await safe_send(
                            int(reward["referrer_id"]),
                            f"🎁 Referral bonus credited: {reward['amount']}\nReferee ID: {reward['referee_id']}\nReason: First approved deposit",
                        )
                    except Exception:
                        logger.exception("Failed sending referral reward message")

                await event.respond(f"Deposit request #{req_id} -> {req['status']}")
                await event.answer("Done")
                return
            if data.startswith("a:"):
                await handle_admin_callback(event, db, cfg.admin_ids, admin_state)
                return

            if data.startswith("u:"):
                await handle_user_action(event, data)
                return

            if not limiter.allow(event.sender_id):
                await event.answer("Slow down", alert=False)
                return

            u = await db.get_user(event.sender_id)
            if u and u.is_banned:
                await event.answer("Banned", alert=True)
                return

            if data == "u:account":
                if not u:
                    await event.respond("Use /start first.", buttons=user_menu())
                    await event.answer()
                    return
                dash_text = await get_ui_text("dashboard_text", DEFAULT_DASHBOARD_TEXT)
                dash_img = await get_ui_image("dashboard_image_path")
                text = (f"{dash_text}\n\n👤 **Account Info:**\nID: `{u.tg_id}`\nBalance: `{u.balance} INR`")
                await send_page(event.chat_id, text, image_path=dash_img, buttons=user_menu())
                await event.answer()

            elif data == "u:buy":
                if not u:
                    await event.respond("Use /start first.", buttons=user_menu())
                    await event.answer()
                    return
                info = await db.get_next_stock_info()
                if not info:
                    await event.respond("❌ Out of stock. Please try later.", buttons=user_menu())
                    await event.answer()
                    return
                
                # Auto detect country with flag
                country = get_country_info(info['example'])
                
                text = (
                    f"🛒 **Confirm Purchase**\n\n"
                    f"🌍 Country: **{country}**\n"
                    f"📦 Total Stock: `{info['total']}`\n"
                    f"💰 Price: `{info['price']} INR`\n\n"
                    f"💳 Your Balance: `{u.balance} INR`"
                )
                buttons = [
                    [Button.inline("✅ Confirm & Buy", b"u:confirm_buy")],
                    [Button.inline("🔙 Cancel", b"u:account")]
                ]
                await event.respond(text, buttons=buttons)
                await event.answer()

            elif data == "u:confirm_buy":
                res = await stock.buy_item(event.sender_id)
                if not res.ok:
                    await event.respond(res.message, buttons=user_menu())
                    await event.answer()
                    return

                phone_display = res.item.replace(".session", "")
                country_info = get_country_info(phone_display)

                await event.respond(
                    f"✅ **Purchase Successful!**\n\n"
                    f"📞 **Number:** `{phone_display}`\n"
                    f"🌍 **Country:** {country_info}\n"
                    f"💰 Price: `{res.price} INR`\n\n"
                    f"⌛ **Bot is now monitoring for OTP...**\n"
                    "Please login. Code will appear here.",
                    buttons=user_menu()
                )

                session_path = Path(cfg.sessions_dir) / res.item
                asyncio.create_task(
                    stock.start_otp_listener(
                        session_path=session_path,
                        user_id=event.sender_id,
                        stock_id=res.stock_id,
                        bot_client=client
                    )
                )
                await event.answer()

            elif data == "u:tx":
                txs = await db.get_transactions(event.sender_id, limit=10)
                if not txs:
                    await event.respond("No transactions yet.", buttons=user_menu())
                    await event.answer()
                    return
                lines = ["Last transactions (latest first):"]
                for t in txs:
                    lines.append(
                        f"#{t.get('id')} | `{t.get('type')}` | `{t.get('amount')}` | `{t.get('created_at')}`\n{(t.get('description') or '').strip()}"
                    )
                await event.respond("\n\n".join(lines), parse_mode="md", buttons=user_menu())
                await event.answer()
                return
                txs = await db.get_transactions(event.sender_id)
                lines = ["📜 **Last transactions:**"] + [f"#{t['id']} {t['type']} {t['amount']} INR" for t in txs]
                await event.respond("\n".join(lines), buttons=user_menu())
                await event.answer()

            elif data == "u:refer":
                u2 = await db.get_user(event.sender_id)
                if not u2:
                    await event.respond("Use /start first.", buttons=user_menu())
                    await event.answer()
                    return
                bonus_raw = await db.get_setting("referral_bonus")
                try:
                    bonus = int(bonus_raw) if bonus_raw else 0
                except Exception:
                    bonus = 0
                total = await db.referrals_count(event.sender_id)
                referred_by = u2.referred_by
                link = f"https://t.me/{bot_username}?start=ref_{event.sender_id}" if bot_username else f"/start ref_{event.sender_id}"
                text = (
                    "Refer & Earn\n\n"
                    f"Your referral link:\n`{link}`\n\n"
                    f"Bonus per referral: `{bonus}`\n"
                    f"Total referrals: `{total}`\n"
                    f"Referred by: `{referred_by if referred_by else '-'}`"
                )
                await event.respond(text, parse_mode="md", buttons=user_menu())
                await event.answer()
                return

            elif data == "u:last":
                last = await db.last_purchase(event.sender_id)
                if not last:
                    await event.respond("No history found.", buttons=user_menu())
                else:
                    phone_display = last['item'].replace(".session", "")
                    country_info = get_country_info(phone_display)
                    otp_info = f"\n🔑 **OTP:** `{last.get('otp_code')}`" if last.get('otp_code') else ""
                    await event.respond(f"📞 **Last purchase:**\nNumber: `{phone_display}`\nCountry: {country_info}{otp_info}", buttons=user_menu())
                await event.answer()

            elif data == "u:deposit":
                text = await get_ui_text("deposit_text", DEFAULT_DEPOSIT_TEXT)
                upi_id = await db.get_setting("deposit_upi_id") or "-"
                usdt_wallet = await db.get_setting("deposit_usdt_wallet") or "-"
                deposit_note = await db.get_setting("deposit_note") or ""
                upi_qr = await db.get_setting("deposit_upi_qr_path")
                full = (
                    f"{text}\n\n"
                    f"⚠️ Deposit manually add by admin.\n\n"
                    f"\U0001F4B3 UPI ID: `{upi_id}`\n"
                    f"\U0001FA99 USDT Wallet: `{usdt_wallet}`\n\n"
                    f"{deposit_note}\n\n"
                    f"\U0001F4B3 Choose a method:"
                )
                # If QR is set, show it on UPI method screen after click.
                await event.respond(
                    full,
                    parse_mode="md",
                    buttons=[[Button.inline("\U0001F4B3 UPI", b"u:dep_upi"), Button.inline("\U0001FA99 USDT", b"u:dep_usdt")]],
                )
                await event.answer()

            elif data == "u:support":
                user_state[event.sender_id] = UserState(waiting_for="support")
                text = await get_ui_text("support_text", DEFAULT_SUPPORT_TEXT)
                await event.respond(text)
                await event.answer()

            elif data == "u:dep_upi":
                upi_id = await db.get_setting("deposit_upi_id") or "-"
                upi_qr = await db.get_setting("deposit_upi_qr_path")
                note = await db.get_setting("deposit_note") or ""
                caption = (
                    "\U0001F4B3 UPI Deposit\n\n"
                    f"UPI ID: `{upi_id}`\n\n"
                    f"{note}\n\n"
                    "Jitna amount bhejna hai bhej do.\n"
                    "Payment ke baad **Submit UTR** button dabao."
                )
                await send_page(
                    event.chat_id,
                    caption,
                    image_path=upi_qr,
                    buttons=[
                        [Button.inline("✅ Submit UTR", b"u:dep_upi_submit")],
                        [Button.inline("🔙 Back", b"u:deposit")],
                    ],
                )
                await event.answer()

            elif data == "u:dep_upi_submit":
                user_state[event.sender_id] = UserState(waiting_for="deposit_upi_amount", deposit_method="UPI", deposit_amount=None)
                await event.respond("💵 Enter amount (number only). Example: `100`", parse_mode="md")
                await event.answer()

            elif data == "u:dep_usdt":
                user_state[event.sender_id] = UserState(waiting_for="deposit_usdt", deposit_method="USDT")
                wallet = await db.get_setting("deposit_usdt_wallet") or "-"
                caption = (
                    "\U0001FA99 USDT Deposit\n\n"
                    f"Wallet: `{wallet}`\n\n"
                    "Now send:\n`amount txid`"
                )
                await event.respond(caption, parse_mode="md", buttons=user_menu())
                await event.answer()

        except Exception:
            logger.exception("Callback error")

    @client.on(events.NewMessage)
    async def on_message(event: events.NewMessage.Event) -> None:
        await handle_admin_message(event, db, cfg.admin_ids, admin_state, cfg.sessions_dir, cfg.default_item_price)
        
        if event.raw_text and event.raw_text.startswith("/"): return
        state = user_state.get(event.sender_id)
        if not (state and state.waiting_for):
            raw = (event.raw_text or "").strip()
            text_to_action = {
                BTN_ACCOUNT: "u:account",
                BTN_TX: "u:tx",
                BTN_BUY: "u:buy",
                BTN_REFER: "u:refer",
                BTN_DEPOSIT: "u:deposit",
                BTN_SUPPORT: "u:support",
                BTN_CONFIRM_BUY: "u:confirm_buy",
                BTN_CANCEL: "u:account",
                BTN_BACK_MENU: "u:account",
                BTN_BACK_DEPOSIT: "u:deposit",
                BTN_UPI: "u:dep_upi",
                BTN_USDT: "u:dep_usdt",
                BTN_SUBMIT_UTR: "u:dep_upi_submit",
            }
            action = text_to_action.get(raw)
            if action:
                await handle_user_action(event, action)
                return
        if state and state.waiting_for:
            waiting = state.waiting_for
            user_state[event.sender_id] = UserState(None)
            if waiting in {"deposit_upi_amount", "deposit_upi_utr", "deposit_usdt"}:
                if not await ensure_user(event):
                    return
                raw = (event.raw_text or "").strip()
                if raw in {BTN_BACK_MENU, BTN_CANCEL}:
                    await handle_user_action(event, "u:account")
                    return
                if raw == BTN_BACK_DEPOSIT:
                    await handle_user_action(event, "u:deposit")
                    return

                if waiting == "deposit_upi_amount":
                    try:
                        amount = int(raw)
                    except Exception:
                        user_state[event.sender_id] = UserState(waiting_for="deposit_upi_amount", deposit_method="UPI", deposit_amount=None)
                        await event.respond("❌ Amount number only. Example: `100`", parse_mode="md")
                        return
                    if amount <= 0:
                        user_state[event.sender_id] = UserState(waiting_for="deposit_upi_amount", deposit_method="UPI", deposit_amount=None)
                        await event.respond("❌ Amount must be > 0.")
                        return
                    user_state[event.sender_id] = UserState(waiting_for="deposit_upi_utr", deposit_method="UPI", deposit_amount=amount)
                    await event.respond("🧾 Now send your UTR number.", buttons=user_menu())
                    return

                if waiting == "deposit_upi_utr":
                    amount = int(state.deposit_amount or 0)
                    utr = raw
                    if not utr:
                        user_state[event.sender_id] = UserState(waiting_for="deposit_upi_utr", deposit_method="UPI", deposit_amount=amount)
                        await event.respond("❌ UTR empty. Send UTR number.")
                        return
                    method = "UPI"
                    reference = utr
                    req_id = await db.create_deposit_request(event.sender_id, amount=amount, method=method, reference=reference)
                    await db.log_transaction(event.sender_id, "deposit_request", amount, f"Request #{req_id} | {method} | UTR={reference}")

                    admin_buttons = [[
                        Button.inline("✅ Accept", f"a:dep_accept:{req_id}".encode("utf-8")),
                        Button.inline("❌ Decline", f"a:dep_decline:{req_id}".encode("utf-8")),
                    ]]
                    admin_text = (
                        f"💰 Deposit request #{req_id}\n"
                        f"👤 User: {event.sender_id}\n"
                        f"💵 Amount: {amount}\n"
                        f"🔹 Method: {method}\n"
                        f"🧾 UTR: {reference}"
                    )

                    # Optional proof media (photo/document) - download and send to admins with buttons
                    proof_path = None
                    if event.message and (event.message.photo or event.message.file):
                        try:
                            Path("media/deposits").mkdir(parents=True, exist_ok=True)
                            fname = event.message.file.name if event.message.file and event.message.file.name else f"proof_{event.sender_id}_{event.message.id}.jpg"
                            dest = Path("media/deposits") / f"{req_id}_{fname}"
                            await event.message.download_media(file=str(dest))
                            proof_path = str(dest)
                            await db.set_deposit_proof(req_id, proof_path)
                        except Exception:
                            logger.exception("Failed downloading deposit proof")

                    for admin_id in cfg.admin_ids:
                        try:
                            if proof_path:
                                await safe_send_file(admin_id, proof_path, caption=admin_text, buttons=admin_buttons)
                            else:
                                await safe_send(admin_id, admin_text, buttons=admin_buttons)
                        except Exception:
                            logger.exception("Failed to notify admin %s", admin_id)

                    await event.respond(f"✅ Deposit request submitted.\nRequest ID: #{req_id}", buttons=user_menu())
                    return

                # deposit_usdt
                parts = raw.split()
                if len(parts) < 2:
                    user_state[event.sender_id] = UserState(waiting_for="deposit_usdt", deposit_method="USDT", deposit_amount=None)
                    await event.respond("Send: `amount txid`", parse_mode="md")
                    return
                try:
                    amount = int(parts[0])
                except Exception:
                    user_state[event.sender_id] = UserState(waiting_for="deposit_usdt", deposit_method="USDT", deposit_amount=None)
                    await event.respond("Invalid amount. Send: `amount txid`", parse_mode="md")
                    return
                if amount <= 0:
                    user_state[event.sender_id] = UserState(waiting_for="deposit_usdt", deposit_method="USDT", deposit_amount=None)
                    await event.respond("Amount must be > 0.")
                    return
                method = "USDT"
                reference = " ".join(parts[1:]).strip()
                req_id = await db.create_deposit_request(event.sender_id, amount=amount, method=method, reference=reference or None)
                await db.log_transaction(event.sender_id, "deposit_request", amount, f"Request #{req_id} | {method} | {reference}".strip())

                admin_buttons = [[
                    Button.inline("✅ Accept", f"a:dep_accept:{req_id}".encode("utf-8")),
                    Button.inline("❌ Decline", f"a:dep_decline:{req_id}".encode("utf-8")),
                ]]
                admin_text = (
                    f"💰 Deposit request #{req_id}\n"
                    f"👤 User: {event.sender_id}\n"
                    f"💵 Amount: {amount}\n"
                    f"🔹 Method: {method}\n"
                    f"🧾 TXID: {reference or '-'}"
                )

                proof_path = None
                if event.message and (event.message.photo or event.message.file):
                    try:
                        Path("media/deposits").mkdir(parents=True, exist_ok=True)
                        fname = event.message.file.name if event.message.file and event.message.file.name else f"proof_{event.sender_id}_{event.message.id}.jpg"
                        dest = Path("media/deposits") / f"{req_id}_{fname}"
                        await event.message.download_media(file=str(dest))
                        proof_path = str(dest)
                        await db.set_deposit_proof(req_id, proof_path)
                    except Exception:
                        logger.exception("Failed downloading deposit proof")

                for admin_id in cfg.admin_ids:
                    try:
                        if proof_path:
                            await safe_send_file(admin_id, proof_path, caption=admin_text, buttons=admin_buttons)
                        else:
                            await safe_send(admin_id, admin_text, buttons=admin_buttons)
                    except Exception:
                        logger.exception("Failed to notify admin %s", admin_id)

                await event.respond(f"✅ Deposit request submitted.\nRequest ID: #{req_id}", buttons=user_menu())
                return
                if not await ensure_user(event):
                    return
                raw = (event.raw_text or "").strip()
                parts = raw.split()
                if len(parts) < 2:
                    user_state[event.sender_id] = UserState(
                        waiting_for=waiting,
                        deposit_method="UPI" if waiting == "deposit_upi" else "USDT",
                    )
                    hint = "`amount reference`" if waiting == "deposit_upi" else "`amount txid`"
                    await event.respond(f"Send: {hint}", parse_mode="md")
                    return
                try:
                    amount = int(parts[0])
                except Exception:
                    user_state[event.sender_id] = UserState(
                        waiting_for=waiting,
                        deposit_method="UPI" if waiting == "deposit_upi" else "USDT",
                    )
                    await event.respond("Invalid amount. Send amount first.", parse_mode="md")
                    return
                if amount <= 0:
                    user_state[event.sender_id] = UserState(
                        waiting_for=waiting,
                        deposit_method="UPI" if waiting == "deposit_upi" else "USDT",
                    )
                    await event.respond("Amount must be > 0.")
                    return

                method = "UPI" if waiting == "deposit_upi" else "USDT"
                reference = " ".join(parts[1:]).strip()
                req_id = await db.create_deposit_request(
                    event.sender_id,
                    amount=amount,
                    method=method,
                    reference=reference or None,
                )
                await db.log_transaction(event.sender_id, "deposit_request", amount, f"Request #{req_id} | {method} | {reference}".strip())

                admin_buttons = [
                    [
                        Button.inline("Accept", f"a:dep_accept:{req_id}".encode("utf-8")),
                        Button.inline("Decline", f"a:dep_decline:{req_id}".encode("utf-8")),
                    ]
                ]
                admin_text = (
                    f"Deposit request #{req_id}\n"
                    f"User: {event.sender_id}\n"
                    f"Amount: {amount}\n"
                    f"Method: {method}\n"
                    f"Reference: {reference or '-'}"
                )
                for admin_id in cfg.admin_ids:
                    try:
                        await safe_send(admin_id, admin_text, buttons=admin_buttons)
                    except Exception:
                        logger.exception("Failed to notify admin %s", admin_id)

                await event.respond(f"✅ Deposit request submitted.\nRequest ID: #{req_id}", buttons=user_menu())
                return
            elif waiting == "support":
                if not await ensure_user(event):
                    return
                text = (event.raw_text or "").strip()
                if text in {BTN_BACK_MENU, BTN_CANCEL}:
                    await handle_user_action(event, "u:account")
                    return
                if text == BTN_BACK_DEPOSIT:
                    await handle_user_action(event, "u:deposit")
                    return
                await db.log_transaction(event.sender_id, "support", 0, text)
                for admin_id in cfg.admin_ids:
                    try:
                        await safe_send(admin_id, f"Support from {event.sender_id}:\n{text}")
                    except Exception:
                        logger.exception("Failed to notify admin %s", admin_id)
                await event.respond("✅ Message sent to Admin.", buttons=user_menu())
                return

    logger.info("Bot is running.")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())

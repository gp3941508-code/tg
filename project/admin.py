from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import phonenumbers
from phonenumbers import geocoder

from telethon import Button, events
from telethon.tl.custom import Message

from database import Database

@dataclass
class AdminState:
    waiting_for: str | None = None  # "add_stock", "stock_number", etc.
    setting_key: str | None = None
    temp_file_path: Path | None = None # Temporary storage for session file
    target_user_id: int | None = None
    picker_action: str | None = None

# --- Helper Function for Auto Detection ---
def get_auto_country(phone: str) -> str:
    """Phone number se Flag aur Country Name detect karta hai."""
    try:
        if not phone.startswith("+"):
            phone = "+" + phone
            
        parsed = phonenumbers.parse(phone, None)
        # Country Name nikalna (e.g., India)
        country_name = geocoder.description_for_number(parsed, "en")
        # Region Code nikalna (e.g., IN, US)
        region_code = phonenumbers.region_code_for_number(parsed)
        
        # Region code ko Flag Emoji mein convert karna
        if region_code:
            flag = "".join(chr(127397 + ord(c)) for c in region_code)
            return f"{flag} {country_name}"
        return "🌍 International"
    except:
        return "🌍 International"

def admin_menu() -> list[list[Button]]:
    return [
        [Button.inline("📤 Add .session to stock", b"a:add_stock"), Button.inline("📊 View stock", b"a:view_stock")],
        [Button.inline("📝 Edit start text", b"a:edit_start_text"), Button.inline("🖼️ Set start image", b"a:set_start_image")],
        [Button.inline("📝 Edit dashboard text", b"a:edit_dashboard_text"), Button.inline("🖼️ Set dashboard image", b"a:set_dashboard_image")],
        [Button.inline("💰 Add balance", b"a:add_balance"), Button.inline("💸 Remove balance", b"a:remove_balance")],
        [Button.inline("🚫 Ban user", b"a:ban"), Button.inline("✅ Unban user", b"a:unban")],
        [Button.inline("📜 View transactions", b"a:view_txs")],
    ]

def admin_menu() -> list[list[Button]]:
    return [
        [Button.inline("Add .session to stock", b"a:add_stock"), Button.inline("View stock", b"a:view_stock")],
        [Button.inline("\U0001F4E5 Deposit requests", b"a:view_deposits"), Button.inline("\U0001F4B3 Deposit methods", b"a:deposit_methods")],
        [Button.inline("\U0001F381 Set referral bonus", b"a:set_referral_bonus"), Button.inline("\U0001F50E View referrals", b"a:view_referrals")],
        [Button.inline("\U0001F39F Redeem codes", b"a:redeem_menu"), Button.inline("\U0001F50E Redeem claims", b"a:redeem_claims")],
        [Button.inline("\u270D\ufe0f Edit start text", b"a:edit_start_text"), Button.inline("\U0001F5BC Set start image", b"a:set_start_image")],
        [Button.inline("\u270D\ufe0f Edit dashboard text", b"a:edit_dashboard_text"), Button.inline("\U0001F5BC Set dashboard image", b"a:set_dashboard_image")],
        [Button.inline("\U0001F4B0 Add balance", b"a:add_balance"), Button.inline("\U0001F4B8 Remove balance", b"a:remove_balance")],
        [Button.inline("\U0001F6AB Ban user", b"a:ban"), Button.inline("\u2705 Unban user", b"a:unban")],
        [Button.inline("\U0001F4DC View transactions", b"a:view_txs")],
    ]


async def _render_user_picker(event: events.CallbackQuery.Event, db: Database, action: str, offset: int = 0) -> None:
    limit = 10
    total = await db.users_count()
    users = await db.list_users(limit=limit, offset=offset)
    if not users:
        await event.edit("No users found.")
        return

    def _label(u: dict) -> str:
        uname = u.get("username")
        name = f"@{uname}" if uname else (u.get("first_name") or str(u.get("tg_id")))
        return f"\U0001F464 {name}"

    buttons: list[list[Button]] = []
    row: list[Button] = []
    for u in users:
        uid = int(u["tg_id"])
        row.append(Button.inline(_label(u), f"a:sel:{action}:{uid}:{offset}".encode("utf-8")))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav: list[Button] = []
    if offset > 0:
        nav.append(Button.inline("\u2b05\ufe0f Prev", f"a:page:{action}:{max(0, offset - limit)}".encode("utf-8")))
    if offset + limit < total:
        nav.append(Button.inline("\u27a1\ufe0f Next", f"a:page:{action}:{offset + limit}".encode("utf-8")))
    if nav:
        buttons.append(nav)
    buttons.append([Button.inline("\u274C Close", b"a:close")])

    await event.edit(f"Select user for: `{action}`\nUsers: {total}", buttons=buttons, parse_mode="md")


async def render_admin_panel(event: events.NewMessage.Event, db: Database) -> None:
    counts = await db.stock_counts()
    await event.respond(
        f"\U0001F6E0 **Admin Panel**\n\n"
        f"\U0001F4CA **Stock Status:**\n"
        f"- Available: `{counts.get('available', 0)}`\n"
        f"- Reserved: `{counts.get('reserved', 0)}`\n"
        f"- Sold: `{counts.get('sold', 0)}`",
        buttons=admin_menu(),
        parse_mode="md",
    )

def _is_admin(tg_id: int, admin_ids: set[int]) -> bool:
    return tg_id in admin_ids

async def handle_admin_callback(
    event: events.CallbackQuery.Event,
    db: Database,
    admin_ids: set[int],
    admin_state: dict[int, AdminState],
) -> None:
    if not _is_admin(event.sender_id, admin_ids):
        await event.answer("Not allowed", alert=True)
        return

    data = (event.data or b"").decode("utf-8", "ignore")

    if data == "a:close":
        await event.edit("Closed.")
        await event.answer()
        return

    if data.startswith("a:page:"):
        parts = data.split(":")
        if len(parts) == 4 and parts[3].isdigit():
            await _render_user_picker(event, db, action=parts[2], offset=int(parts[3]))
            await event.answer()
            return

    if data.startswith("a:sel:"):
        parts = data.split(":")
        if len(parts) == 5 and parts[3].isdigit() and parts[4].isdigit():
            action = parts[2]
            uid = int(parts[3])
            if action in {"addbal", "rembal"}:
                admin_state[event.sender_id] = AdminState(waiting_for="amount", target_user_id=uid, picker_action=action)
                await event.respond(f"Send amount for `{action}` to user `{uid}`.\nExample: `100`", parse_mode="md")
                await event.answer()
                return
            if action in {"ban", "unban"}:
                await db.upsert_user(uid, None, None)
                await db.set_ban(uid, action == "ban")
                try:
                    await event.client.send_message(uid, "🚫 You have been banned by admin." if action == "ban" else "✅ You have been unbanned by admin.")
                except Exception:
                    pass
                await event.respond(f"Done: `{action}` for `{uid}`", parse_mode="md")
                await event.answer()
                return
            if action == "refs":
                count = await db.referrals_count(uid)
                refs = await db.list_referrals(uid, limit=20)
                lines = [f"Referrals for {uid}: {count} total"]
                for r in refs:
                    lines.append(f"#{r['id']} referee={r['referee_id']} bonus={r['bonus_amount']} at={r['created_at']}")
                await event.respond("\n".join(lines))
                await event.answer()
                return
    
    if data == "a:add_stock":
        admin_state[event.sender_id] = AdminState(waiting_for="add_stock")
        await event.respond("📂 Please send the **.session** file first.")
        await event.answer()
        return

    if data == "a:view_stock":
        counts = await db.stock_counts()
        await event.respond(
            f"📊 **Current Stock**\n"
            f"Available: {counts.get('available', 0)}\n"
            f"Reserved: {counts.get('reserved', 0)}\n"
            f"Sold: {counts.get('sold', 0)}"
        )
        await event.answer()
        return

    if data == "a:edit_start_text":
        admin_state[event.sender_id] = AdminState(waiting_for="set_text", setting_key="start_text")
        await event.respond("Send new /start text.")
        await event.answer()
        return

    if data == "a:set_start_image":
        admin_state[event.sender_id] = AdminState(waiting_for="set_image", setting_key="start_image_path")
        await event.respond("Send start image (photo/file).")
        await event.answer()
        return

    if data == "a:edit_dashboard_text":
        admin_state[event.sender_id] = AdminState(waiting_for="set_text", setting_key="dashboard_text")
        await event.respond("Send new dashboard text.")
        await event.answer()
        return

    if data == "a:set_dashboard_image":
        admin_state[event.sender_id] = AdminState(waiting_for="set_image", setting_key="dashboard_image_path")
        await event.respond("Send dashboard image (photo/file).")
        await event.answer()
        return

    if data == "a:set_referral_bonus":
        admin_state[event.sender_id] = AdminState(waiting_for="set_text", setting_key="referral_bonus")
        await event.respond("Send referral bonus amount (number). Example: `10`", parse_mode="md")
        await event.answer()
        return

    if data == "a:view_referrals":
        await _render_user_picker(event, db, action="refs", offset=0)
        await event.answer()
        return

    if data == "a:deposit_methods":
        await event.respond(
            "Deposit methods settings:",
            buttons=[
                [Button.inline("\U0001F4B3 Set UPI ID", b"a:set_upi"), Button.inline("\U0001F4F7 Set UPI QR", b"a:set_upi_qr")],
                [Button.inline("\U0001FA99 USDT Options", b"a:usdt_opts"), Button.inline("\U0001FA99 Set USDT Wallet", b"a:set_usdt")],
                [Button.inline("Set Deposit Note", b"a:set_deposit_note")],
                [Button.inline("Set Min Deposit", b"a:set_min_deposit"), Button.inline("Set USDT Rate", b"a:set_usdt_rate")],
            ],
        )
        await event.answer()
        return

    if data == "a:usdt_opts":
        await event.respond(
            "USDT options:",
            buttons=[
                [Button.inline("Add option", b"a:usdt_add"), Button.inline("List options", b"a:usdt_list")],
                [Button.inline("Clear options", b"a:usdt_clear")],
            ],
        )
        await event.answer()
        return

    if data == "a:usdt_add":
        admin_state[event.sender_id] = AdminState(waiting_for="usdt_add_option")
        await event.respond("Send: `Name | Address | MinUSDT`\nExample: `TRC20 | Txxx... | 10`", parse_mode="md")
        await event.answer()
        return

    if data == "a:usdt_list":
        raw = await db.get_setting("deposit_usdt_options") or ""
        try:
            data = json.loads(raw) if raw else []
        except Exception:
            data = []
        if not data:
            await event.respond("No USDT options configured.")
        else:
            lines = ["USDT options:"]
            for i, opt in enumerate(data, 1):
                name = opt.get("name", "-")
                addr = opt.get("address", "-")
                minu = opt.get("min_usdt", "-")
                lines.append(f"{i}. {name} | {addr} | min {minu}")
            await event.respond("\n".join(lines))
        await event.answer()
        return

    if data == "a:usdt_clear":
        await db.set_setting("deposit_usdt_options", "[]")
        await event.respond("USDT options cleared.")
        await event.answer()
        return

    if data == "a:set_upi":
        admin_state[event.sender_id] = AdminState(waiting_for="set_text", setting_key="deposit_upi_id")
        await event.respond("Send new UPI ID (text).")
        await event.answer()
        return

    if data == "a:set_upi_qr":
        admin_state[event.sender_id] = AdminState(waiting_for="set_image", setting_key="deposit_upi_qr_path")
        await event.respond("Send UPI QR image (photo/file).")
        await event.answer()
        return

    if data == "a:set_usdt":
        admin_state[event.sender_id] = AdminState(waiting_for="set_text", setting_key="deposit_usdt_wallet")
        await event.respond("Send new USDT wallet address (text).")
        await event.answer()
        return

    if data == "a:set_deposit_note":
        admin_state[event.sender_id] = AdminState(waiting_for="set_text", setting_key="deposit_note")
        await event.respond("Send deposit note/instructions (text).")
        await event.answer()
        return

    if data == "a:set_min_deposit":
        admin_state[event.sender_id] = AdminState(waiting_for="set_text", setting_key="min_deposit_inr")
        await event.respond("Send minimum deposit in INR (number). Example: `50`", parse_mode="md")
        await event.answer()
        return

    if data == "a:set_usdt_rate":
        admin_state[event.sender_id] = AdminState(waiting_for="set_text", setting_key="usdt_rate_inr")
        await event.respond("Send USDT rate in INR (number). Example: `94`", parse_mode="md")
        await event.answer()
        return

    if data == "a:redeem_menu":
        await event.respond(
            "Redeem codes:",
            buttons=[
                [Button.inline("Create code", b"a:redeem_create"), Button.inline("List codes", b"a:redeem_list")],
                [Button.inline("View claims", b"a:redeem_claims")],
            ],
        )
        await event.answer()
        return

    if data == "a:redeem_create":
        admin_state[event.sender_id] = AdminState(waiting_for="redeem_create")
        await event.respond("Send: `CODE AMOUNT [MAX_USES]`\nExample: `WELCOME50 50 1`", parse_mode="md")
        await event.answer()
        return

    if data == "a:redeem_list":
        codes = await db.list_redeem_codes(limit=15)
        if not codes:
            await event.respond("No redeem codes found.")
            await event.answer()
            return
        lines = ["Redeem codes (latest first):"]
        for c in codes:
            status = "active" if int(c.get("is_active", 1)) == 1 else "inactive"
            lines.append(
                f"{c['code']} | amount={c['amount']} | used={c['used_count']}/{c['max_uses']} | {status}"
            )
        await event.respond("\n".join(lines))
        await event.answer()
        return

    if data == "a:redeem_claims":
        claims = await db.list_redeem_claims(limit=20)
        if not claims:
            await event.respond("No redeem claims yet.")
            await event.answer()
            return
        lines = ["Redeem claims (latest first):"]
        for c in claims:
            lines.append(f"{c['code']} | user={c['tg_id']} | amount={c['amount']} | at={c['claimed_at']}")
        await event.respond("\n".join(lines))
        await event.answer()
        return

    if data == "a:view_deposits":
        reqs = await db.list_deposit_requests(status="pending", limit=10)
        if not reqs:
            await event.respond("No pending deposit requests.")
            await event.answer()
            return
        for r in reqs:
            rid = int(r["id"])
            buttons = [
                [
                    Button.inline("Accept", f"a:dep_accept:{rid}".encode("utf-8")),
                    Button.inline("Decline", f"a:dep_decline:{rid}".encode("utf-8")),
                ]
            ]
            note = r.get("note") or "-"
            await event.respond(
                f"Deposit request #{rid}\n"
                f"User: {r['tg_id']}\n"
                f"Amount: {r['amount']}\n"
                f"Method: {r['method']}\n"
                f"Reference: {r.get('reference') or '-'}\n"
                f"Note: {note}",
                buttons=buttons,
            )
        await event.answer()
        return

    if data == "a:add_balance":
        await _render_user_picker(event, db, action="addbal", offset=0)
        await event.answer()
        return

    if data == "a:remove_balance":
        await _render_user_picker(event, db, action="rembal", offset=0)
        await event.answer()
        return

    if data == "a:ban":
        await _render_user_picker(event, db, action="ban", offset=0)
        await event.answer()
        return

    if data == "a:unban":
        await _render_user_picker(event, db, action="unban", offset=0)
        await event.answer()
        return

    if data == "a:view_txs":
        txs = await db.get_all_transactions(limit=20)
        if not txs:
            await event.respond("No transactions.")
        else:
            msg = "\n".join([f"#{t['id']} {t['tg_id']} {t['type']} {t['amount']}" for t in txs])
            await event.respond(msg)
        await event.answer()
        return


async def handle_admin_message(
    event: events.NewMessage.Event,
    db: Database,
    admin_ids: set[int],
    admin_state: dict[int, AdminState],
    sessions_dir: str,
    default_price: int,
) -> None:
    if event.sender_id not in admin_ids:
        return

    state = admin_state.get(event.sender_id)
    if not state or not state.waiting_for:
        return

    waiting = state.waiting_for

    # STEP 1: Handle Session File
    if waiting == "add_stock":
        msg: Message = event.message
        if not msg.file or not msg.file.name.endswith(".session"):
            await event.respond("❌ Please send a valid **.session** file.")
            return

        dest_dir = Path(sessions_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / msg.file.name

        await msg.download_media(file=str(dest_path))
        
        state.temp_file_path = dest_path
        state.waiting_for = "stock_number"
        await event.respond(f"✅ File received: `{msg.file.name}`\n\n📞 Now send the **Phone Number** (e.g., +919876543210):")
        return

    # STEP 2: Handle Phone Number & Auto-Save
    if waiting == "stock_number":
        phone_number = event.raw_text.strip()
        session_file_name = state.temp_file_path.name
        
        # --- Automatic Detection ---
        full_country_info = get_auto_country(phone_number)
        
        # Database mein entry
        added = await db.add_stock_items([session_file_name], price=default_price)
        
        if added > 0:
            await event.respond(
                f"✅ **Stock Added Successfully!**\n\n"
                f"📂 File: `{session_file_name}`\n"
                f"📞 Number: `{phone_number}`\n"
                f"🌍 Detected: {full_country_info}\n"
                f"💰 Price: `{default_price} INR`"
            )
        else:
            await event.respond("⚠️ Database error: File already exists in stock.")
        
        # Process complete, clear state
        admin_state[event.sender_id] = AdminState(waiting_for=None)


    # view referrals info
    if waiting == "view_referrals":
        raw = (event.raw_text or "").strip()
        if not raw.isdigit():
            await event.respond("Send numeric user_id.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        uid = int(raw)
        count = await db.referrals_count(uid)
        refs = await db.list_referrals(uid, limit=20)
        lines = [f"Referrals for {uid}: {count} total"]
        for r in refs:
            lines.append(f"#{r['id']} referee={r['referee_id']} bonus={r['bonus_amount']} at={r['created_at']}")
        await event.respond("\n".join(lines))
        admin_state[event.sender_id] = AdminState(waiting_for=None)
        return

    if waiting == "redeem_create":
        raw = (event.raw_text or "").strip()
        parts = raw.split()
        if len(parts) < 2:
            await event.respond("Use: `CODE AMOUNT [MAX_USES]`", parse_mode="md")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        code = parts[0].strip().upper()
        try:
            amount = int(parts[1])
        except Exception:
            await event.respond("Amount must be numeric.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        if amount <= 0:
            await event.respond("Amount must be > 0.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        max_uses = 1
        if len(parts) >= 3:
            try:
                max_uses = int(parts[2])
            except Exception:
                await event.respond("Max uses must be numeric.")
                admin_state[event.sender_id] = AdminState(waiting_for=None)
                return
        if max_uses <= 0:
            await event.respond("Max uses must be > 0.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        res = await db.create_redeem_code(code, amount, max_uses, created_by=event.sender_id)
        if not res.get("ok"):
            reason = res.get("reason") or "failed"
            await event.respond(f"Failed to create code: {reason}")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        await event.respond(f"✅ Redeem code created: `{code}` | amount={amount} | max_uses={max_uses}", parse_mode="md")
        admin_state[event.sender_id] = AdminState(waiting_for=None)
        return

    if waiting == "usdt_add_option":
        raw = (event.raw_text or "").strip()
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 3:
            await event.respond("Use: `Name | Address | MinUSDT`", parse_mode="md")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        name, address, min_raw = parts[0], parts[1], parts[2]
        if not name or not address:
            await event.respond("Name and Address are required.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        try:
            min_usdt = Decimal(min_raw)
            if min_usdt <= 0:
                raise ValueError("non_positive")
        except (InvalidOperation, ValueError):
            await event.respond("MinUSDT must be a positive number.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return

        raw_opts = await db.get_setting("deposit_usdt_options") or ""
        try:
            data = json.loads(raw_opts) if raw_opts else []
        except Exception:
            data = []
        if not isinstance(data, list):
            data = []
        data.append({"name": name, "address": address, "min_usdt": str(min_usdt)})
        await db.set_setting("deposit_usdt_options", json.dumps(data))
        await event.respond(f"Added USDT option: {name} | min {min_usdt}")
        admin_state[event.sender_id] = AdminState(waiting_for=None)
        return

    if waiting == "amount":
        uid = state.target_user_id
        action = state.picker_action
        if not uid or action not in {"addbal", "rembal"}:
            await event.respond("Invalid state. Try again from /admin.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        raw = (event.raw_text or "").strip()
        try:
            amt = int(raw)
        except Exception:
            await event.respond("Send numeric amount. Example: `100`", parse_mode="md")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        if amt <= 0:
            await event.respond("Amount must be > 0.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return

        await db.upsert_user(uid, None, None)
        if action == "addbal":
            await db.add_balance(uid, amt, f"Admin {event.sender_id}")
            await event.respond(f"✅ Added {amt} to {uid}.")
            try:
                await event.client.send_message(uid, f"✅ Balance added: {amt}\nBy admin: {event.sender_id}")
            except Exception:
                pass
        else:
            await db.remove_balance(uid, amt, f"Admin {event.sender_id}")
            await event.respond(f"✅ Removed {amt} from {uid}.")
            try:
                await event.client.send_message(uid, f"⚠️ Balance removed: {amt}\nBy admin: {event.sender_id}")
            except Exception:
                pass

        admin_state[event.sender_id] = AdminState(waiting_for=None)
        return

    # --- Generic Handlers ---
    if waiting == "set_text":
        # Special validation for numeric referral bonus
        if state.setting_key == "referral_bonus":
            raw = (event.raw_text or "").strip()
            try:
                bonus = int(raw)
                if bonus < 0:
                    raise ValueError("negative")
            except Exception:
                await event.respond("Referral bonus must be a non-negative number.")
                admin_state[event.sender_id] = AdminState(waiting_for=None)
                return
            await db.set_setting("referral_bonus", str(bonus))
            await event.respond("✅ Referral bonus updated.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        if state.setting_key == "min_deposit_inr":
            raw = (event.raw_text or "").strip()
            try:
                minimum = int(raw)
                if minimum <= 0:
                    raise ValueError("non_positive")
            except Exception:
                await event.respond("Minimum deposit must be a positive number.")
                admin_state[event.sender_id] = AdminState(waiting_for=None)
                return
            await db.set_setting("min_deposit_inr", str(minimum))
            await event.respond("✅ Minimum deposit updated.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        if state.setting_key == "usdt_rate_inr":
            raw = (event.raw_text or "").strip()
            try:
                rate = Decimal(raw)
                if rate <= 0:
                    raise ValueError("non_positive")
            except (InvalidOperation, ValueError):
                await event.respond("USDT rate must be a positive number.")
                admin_state[event.sender_id] = AdminState(waiting_for=None)
                return
            await db.set_setting("usdt_rate_inr", str(rate))
            await event.respond("✅ USDT rate updated.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        if not state.setting_key:
            await event.respond("❌ Missing setting key.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        await db.set_setting(state.setting_key, event.raw_text.strip())
        await event.respond("✅ Text updated.")
        admin_state[event.sender_id] = AdminState(waiting_for=None)
    
    elif waiting == "add_balance":
        try:
            parts = (event.raw_text or "").split()
            uid, amt = int(parts[0]), int(parts[1])
            await db.upsert_user(uid, None, None)
            await db.add_balance(uid, amt, f"Admin {event.sender_id}")
            await event.respond(f"✅ Added {amt} to {uid}.")
            try:
                await event.client.send_message(uid, f"✅ Balance added: {amt}\nBy admin: {event.sender_id}")
            except Exception:
                pass
        except Exception:
            await event.respond("❌ Use: `user_id amount`")
        admin_state[event.sender_id] = AdminState(waiting_for=None)
        return
        try:
            parts = event.raw_text.split()
            uid, amt = int(parts[0]), int(parts[1])
            await db.add_balance(uid, amt, f"Admin {event.sender_id}")
            await event.respond(f"✅ Added {amt} to {uid}.")
        except:
            await event.respond("❌ Use: `user_id amount`")
        admin_state[event.sender_id] = AdminState(waiting_for=None)

    elif waiting == "remove_balance":
        try:
            parts = (event.raw_text or "").split()
            uid, amt = int(parts[0]), int(parts[1])
            await db.upsert_user(uid, None, None)
            await db.remove_balance(uid, amt, f"Admin {event.sender_id}")
            await event.respond(f"✅ Removed {amt} from {uid}.")
            try:
                await event.client.send_message(uid, f"⚠️ Balance removed: {amt}\nBy admin: {event.sender_id}")
            except Exception:
                pass
        except Exception:
            await event.respond("❌ Use: `user_id amount`")
        admin_state[event.sender_id] = AdminState(waiting_for=None)
        return
        try:
            parts = event.raw_text.split()
            uid, amt = int(parts[0]), int(parts[1])
            await db.upsert_user(uid, None, None)
            await db.remove_balance(uid, amt, f"Admin {event.sender_id}")
            await event.respond(f"✅ Removed {amt} from {uid}.")
        except:
            await event.respond("❌ Use: `user_id amount`")
        admin_state[event.sender_id] = AdminState(waiting_for=None)

    elif waiting == "ban":
        raw = (event.raw_text or "").strip()
        if not raw.isdigit():
            await event.respond("❌ Send numeric user_id.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        uid = int(raw)
        await db.upsert_user(uid, None, None)
        await db.set_ban(uid, True)
        await event.respond(f"✅ Banned {uid}.")
        try:
            await event.client.send_message(uid, "🚫 You have been banned by admin.")
        except Exception:
            pass
        admin_state[event.sender_id] = AdminState(waiting_for=None)
        return
        raw = event.raw_text.strip()
        if not raw.isdigit():
            await event.respond("❌ Send numeric user_id.")
        else:
            uid = int(raw)
            await db.upsert_user(uid, None, None)
            await db.set_ban(uid, True)
            await event.respond(f"✅ Banned {uid}.")
        admin_state[event.sender_id] = AdminState(waiting_for=None)

    elif waiting == "unban":
        raw = (event.raw_text or "").strip()
        if not raw.isdigit():
            await event.respond("❌ Send numeric user_id.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        uid = int(raw)
        await db.upsert_user(uid, None, None)
        await db.set_ban(uid, False)
        await event.respond(f"✅ Unbanned {uid}.")
        try:
            await event.client.send_message(uid, "✅ You have been unbanned by admin.")
        except Exception:
            pass
        admin_state[event.sender_id] = AdminState(waiting_for=None)
        return
        raw = event.raw_text.strip()
        if not raw.isdigit():
            await event.respond("❌ Send numeric user_id.")
        else:
            uid = int(raw)
            await db.upsert_user(uid, None, None)
            await db.set_ban(uid, False)
            await event.respond(f"✅ Unbanned {uid}.")
        admin_state[event.sender_id] = AdminState(waiting_for=None)

    elif waiting == "set_image":
        if not state.setting_key:
            await event.respond("❌ Missing setting key.")
            admin_state[event.sender_id] = AdminState(waiting_for=None)
            return
        msg: Message = event.message
        if not msg.file and not msg.photo:
            await event.respond("❌ Send an image (photo/file).")
            return
        media_dir = Path("media")
        media_dir.mkdir(parents=True, exist_ok=True)
        filename = msg.file.name if msg.file and msg.file.name else f"{state.setting_key}_{msg.id}.jpg"
        dest_path = media_dir / filename
        await msg.download_media(file=str(dest_path))
        await db.set_setting(state.setting_key, str(dest_path))
        await event.respond(f"✅ Image updated: `{dest_path}`")
        admin_state[event.sender_id] = AdminState(waiting_for=None)

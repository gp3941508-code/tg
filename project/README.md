# Telethon Storefront Bot (Safe Template)

This project is a **storefront-style** Telegram bot built with **Telethon**.

## What this template does

- User panel: account, balance, deposit request, support, purchase history, last purchase, buy item from stock
- Admin panel: stock upload/view, add/remove balance, ban/unban, view transactions
- Admin CMS: edit start/dashboard/deposit/support texts + set start/dashboard images
- SQLite database (`aiosqlite`)
- Rate limiting + banned-user protection
- File + console logging

## What this template does NOT do

It **does not** implement OTP interception (e.g., reading codes from `777000`) or any system that sells/forwards login OTPs or uses user session files to access private messages.

## Setup

1. Create a virtual environment (recommended)

   - Windows PowerShell:
     - `python -m venv .venv`
     - `.\\.venv\\Scripts\\Activate.ps1`

2. Install dependencies:
   - `pip install -r requirements.txt`

3. Create a `.env` file in `project/`:

   ```env
   API_ID=123456
   API_HASH=your_api_hash
   BOT_TOKEN=123456:ABCDEF...
   ADMIN_IDS=11111111,22222222
   DEFAULT_ITEM_PRICE=10
   ```

4. Run the bot:
   - `python bot.py`

## Admin usage

- `/admin` opens the admin panel.
- Upload a `.txt` file (one item per line) via **Add stock file**.
- Use **Add balance** / **Remove balance** to manage user balances.
- Use **Edit start text** / **Set start image** to customize the user landing page.
- Use **Edit dashboard text** / **Set dashboard image** to customize the account page header.

## Media storage

- Uploaded images are saved under `project/media/` and their local path is stored in the `settings` table.

## Notes

- Stock items are stored in the `numbers_stock` table (name kept to match your requirement).
- Deposits are recorded as `deposit_request` transactions; admins approve by adding balance.

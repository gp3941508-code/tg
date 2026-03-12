from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
import aiosqlite

@dataclass(frozen=True)
class User:
    tg_id: int
    username: str | None
    first_name: str | None
    balance: int
    is_banned: bool
    created_at: str | None = None
    referred_by: int | None = None

class Database:
    def __init__(self, path: str) -> None:
        self._path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            
            # Users Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER NOT NULL UNIQUE,
                    username TEXT,
                    first_name TEXT,
                    balance INTEGER NOT NULL DEFAULT 0,
                    is_banned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    referred_by INTEGER
                );
            """)

            # Auto-migration for referred_by
            try:
                await db.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER;")
            except aiosqlite.OperationalError:
                pass
            
            # Transactions Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    description TEXT,
                    created_at TEXT NOT NULL
                );
            """)
            
            # Numbers Stock Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS numbers_stock (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'available',
                    sold_to INTEGER,
                    sold_at TEXT,
                    otp_code TEXT,
                    created_at TEXT NOT NULL
                );
            """)
            
            # Auto-Migration for otp_code
            try:
                await db.execute("ALTER TABLE numbers_stock ADD COLUMN otp_code TEXT;")
            except aiosqlite.OperationalError:
                pass

            # Settings Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # Referrals
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER NOT NULL,
                    referee_id INTEGER NOT NULL UNIQUE,
                    bonus_amount INTEGER NOT NULL DEFAULT 0,
                    rewarded_amount INTEGER NOT NULL DEFAULT 0,
                    rewarded_at TEXT,
                    rewarded_deposit_id INTEGER,
                    created_at TEXT NOT NULL
                );
                """
            )

            # Auto-migration for referrals reward tracking
            for stmt in (
                "ALTER TABLE referrals ADD COLUMN rewarded_amount INTEGER NOT NULL DEFAULT 0;",
                "ALTER TABLE referrals ADD COLUMN rewarded_at TEXT;",
                "ALTER TABLE referrals ADD COLUMN rewarded_deposit_id INTEGER;",
            ):
                try:
                    await db.execute(stmt)
                except aiosqlite.OperationalError:
                    pass

            # Deposit requests
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS deposit_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    method TEXT NOT NULL,
                    reference TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by INTEGER,
                    note TEXT,
                    proof_path TEXT
                );
                """
            )

            # Auto-migration for deposit proof
            try:
                await db.execute("ALTER TABLE deposit_requests ADD COLUMN proof_path TEXT;")
            except aiosqlite.OperationalError:
                pass
            await db.commit()

    # --- NEW: Confirmation Screen ke liye stock info fetch karna ---
    async def get_next_stock_info(self) -> dict | None:
        """Agla available item, price aur total stock count return karta hai."""
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            
            # Agla available item fetch karein (Price aur prefix ke liye)
            cur_item = await db.execute(
                "SELECT item, price FROM numbers_stock WHERE status='available' ORDER BY id ASC LIMIT 1;"
            )
            item_row = await cur_item.fetchone()
            
            # Total available count fetch karein
            cur_count = await db.execute(
                "SELECT COUNT(*) as total FROM numbers_stock WHERE status='available';"
            )
            count_row = await cur_count.fetchone()
            
            if not item_row or count_row["total"] == 0:
                return None
                
            return {
                "price": int(item_row["price"]),
                "total": int(count_row["total"]),
                "example": item_row["item"]
            }

    async def upsert_user(self, tg_id: int, username: str | None, first_name: str | None) -> None:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self._path) as db:
            await db.execute("""
                INSERT INTO users (tg_id, username, first_name, balance, is_banned, created_at)
                VALUES (?, ?, ?, 0, 0, ?)
                ON CONFLICT(tg_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name;
                """, (tg_id, username, first_name, now))
            await db.commit()

    async def get_user(self, tg_id: int) -> User | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT tg_id, username, first_name, balance, is_banned, created_at, referred_by FROM users WHERE tg_id=?;",
                (tg_id,),
            )
            row = await cur.fetchone()
            if not row: return None
            return User(
                tg_id=int(row["tg_id"]),
                username=row["username"],
                first_name=row["first_name"],
                balance=int(row["balance"]),
                is_banned=bool(row["is_banned"]),
                created_at=row["created_at"],
                referred_by=int(row["referred_by"]) if row["referred_by"] is not None else None,
            )

    async def add_balance(self, tg_id: int, amount: int, description: str) -> None:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self._path) as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id=?;", (amount, tg_id))
            await db.execute(
                "INSERT INTO transactions (tg_id, type, amount, description, created_at) VALUES (?, ?, ?, ?, ?);",
                (tg_id, "balance_add", amount, description, now),
            )
            await db.commit()

    async def remove_balance(self, tg_id: int, amount: int, description: str) -> None:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self._path) as db:
            await db.execute("UPDATE users SET balance = balance - ? WHERE tg_id=?;", (amount, tg_id))
            await db.execute(
                "INSERT INTO transactions (tg_id, type, amount, description, created_at) VALUES (?, ?, ?, ?, ?);",
                (tg_id, "balance_remove", -abs(amount), description, now),
            )
            await db.commit()

    async def update_otp(self, stock_id: int, otp_code: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("UPDATE numbers_stock SET otp_code=? WHERE id=?;", (otp_code, stock_id))
            await db.commit()

    async def purchase_one_available(self, tg_id: int) -> dict:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE;")
            
            cur_u = await db.execute("SELECT balance, is_banned FROM users WHERE tg_id=?;", (tg_id,))
            user_row = await cur_u.fetchone()
            if not user_row:
                await db.execute("ROLLBACK;")
                return {"ok": False, "reason": "no_user"}
            if bool(user_row["is_banned"]):
                await db.execute("ROLLBACK;")
                return {"ok": False, "reason": "banned"}

            cur_s = await db.execute(
                "SELECT id, item, price FROM numbers_stock WHERE status='available' ORDER BY id ASC LIMIT 1;"
            )
            stock_row = await cur_s.fetchone()
            if not stock_row:
                await db.execute("ROLLBACK;")
                return {"ok": False, "reason": "out_of_stock"}

            price = int(stock_row["price"])
            if int(user_row["balance"]) < price:
                await db.execute("ROLLBACK;")
                return {"ok": False, "reason": "insufficient_balance", "price": price}

            stock_id = int(stock_row["id"])
            await db.execute("UPDATE users SET balance = balance - ? WHERE tg_id=?;", (price, tg_id))
            await db.execute(
                "UPDATE numbers_stock SET status='sold', sold_to=?, sold_at=? WHERE id=?;",
                (tg_id, now, stock_id),
            )
            await db.execute(
                "INSERT INTO transactions (tg_id, type, amount, description, created_at) VALUES (?, ?, ?, ?, ?);",
                (tg_id, "purchase", -price, f"Purchased stock id={stock_id}", now),
            )
            await db.commit()
            return {"ok": True, "item": stock_row["item"], "price": price, "stock_id": stock_id}

    async def get_setting(self, key: str) -> str | None:
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute("SELECT value FROM settings WHERE key=?;", (key,))
            row = await cur.fetchone()
            return str(row[0]) if row else None

    async def set_setting(self, key: str, value: str) -> None:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self._path) as db:
            await db.execute("""
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at;
            """, (key, value, now))
            await db.commit()

    async def stock_counts(self) -> dict[str, int]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT status, COUNT(*) AS c FROM numbers_stock GROUP BY status;")
            rows = await cur.fetchall()
            out = {"available": 0, "sold": 0}
            for r in rows: out[str(r["status"])] = int(r["c"])
            return out

    async def add_stock_items(self, items: list[str], price: int) -> int:
        now = dt.datetime.utcnow().isoformat()
        cleaned = [i.strip() for i in items if i and i.strip()]
        if not cleaned: return 0
        async with aiosqlite.connect(self._path) as db:
            await db.executemany(
                "INSERT INTO numbers_stock (item, price, status, created_at) VALUES (?, ?, 'available', ?);",
                [(i, price, now) for i in cleaned],
            )
            await db.commit()
        return len(cleaned)

    async def last_purchase(self, tg_id: int) -> dict | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, item, price, sold_at, otp_code FROM numbers_stock WHERE sold_to=? ORDER BY id DESC LIMIT 1;",
                (tg_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def log_transaction(self, tg_id: int, type: str, amount: int, description: str) -> None:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT INTO transactions (tg_id, type, amount, description, created_at) VALUES (?, ?, ?, ?, ?);",
                (tg_id, type, amount, description, now),
            )
            await db.commit()

    async def get_transactions(self, tg_id: int, limit: int = 5) -> list[dict]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM transactions WHERE tg_id=? ORDER BY id DESC LIMIT ?;",
                (tg_id, limit),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_all_transactions(self, limit: int = 20) -> list[dict]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, tg_id, type, amount, description, created_at FROM transactions ORDER BY id DESC LIMIT ?;",
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def set_ban(self, tg_id: int, banned: bool) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("UPDATE users SET is_banned=? WHERE tg_id=?;", (1 if banned else 0, tg_id))
            await db.commit()

    async def users_count(self) -> int:
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM users;")
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def list_users(self, limit: int = 20, offset: int = 0) -> list[dict]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT tg_id, username, first_name, balance, is_banned, created_at
                FROM users
                ORDER BY id DESC
                LIMIT ? OFFSET ?;
                """,
                (int(limit), int(offset)),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def referrals_count(self, referrer_id: int) -> int:
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?;", (referrer_id,))
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def referral_apply(self, referee_id: int, referrer_id: int, bonus_amount: int) -> dict:
        """
        Apply referral once for referee_id.
        Returns:
          {"ok": True}
          {"ok": False, "reason": "..."}
        """
        if referee_id == referrer_id:
            return {"ok": False, "reason": "self"}
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE;")

            cur_ref = await db.execute("SELECT referred_by FROM users WHERE tg_id=?;", (referee_id,))
            ref_row = await cur_ref.fetchone()
            if not ref_row:
                await db.execute("ROLLBACK;")
                return {"ok": False, "reason": "no_referee"}
            if ref_row["referred_by"] is not None:
                await db.execute("ROLLBACK;")
                return {"ok": False, "reason": "already"}

            cur_r = await db.execute("SELECT tg_id FROM users WHERE tg_id=?;", (referrer_id,))
            r_row = await cur_r.fetchone()
            if not r_row:
                await db.execute("ROLLBACK;")
                return {"ok": False, "reason": "no_referrer"}

            await db.execute("UPDATE users SET referred_by=? WHERE tg_id=?;", (referrer_id, referee_id))
            await db.execute(
                "INSERT INTO referrals (referrer_id, referee_id, bonus_amount, created_at) VALUES (?, ?, ?, ?);",
                (referrer_id, referee_id, int(bonus_amount), now),
            )

            await db.commit()
            return {"ok": True}

    async def apply_referral_reward_on_deposit(self, referee_id: int, deposit_request_id: int) -> dict | None:
        """
        If referee_id was referred and hasn't rewarded yet, credit referrer once.
        Reward amount is read from settings key: referral_bonus (at reward time).
        Returns dict with reward info or None.
        """
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE;")

            cur_u = await db.execute("SELECT referred_by FROM users WHERE tg_id=?;", (referee_id,))
            urow = await cur_u.fetchone()
            if not urow or urow["referred_by"] is None:
                await db.execute("ROLLBACK;")
                return None
            referrer_id = int(urow["referred_by"])

            cur_s = await db.execute("SELECT value FROM settings WHERE key='referral_bonus';")
            srow = await cur_s.fetchone()
            try:
                reward_amount = int(srow["value"]) if srow else 0
            except Exception:
                reward_amount = 0
            if reward_amount <= 0:
                await db.execute("ROLLBACK;")
                return None

            # Ensure referral row exists and is not rewarded yet
            cur_r = await db.execute("SELECT id, rewarded_at FROM referrals WHERE referee_id=?;", (referee_id,))
            rrow = await cur_r.fetchone()
            if not rrow:
                await db.execute(
                    "INSERT INTO referrals (referrer_id, referee_id, bonus_amount, created_at) VALUES (?, ?, 0, ?);",
                    (referrer_id, referee_id, now),
                )
                cur_r = await db.execute("SELECT id, rewarded_at FROM referrals WHERE referee_id=?;", (referee_id,))
                rrow = await cur_r.fetchone()

            if rrow and rrow["rewarded_at"] is not None:
                await db.execute("ROLLBACK;")
                return None

            await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id=?;", (reward_amount, referrer_id))
            await db.execute(
                "INSERT INTO transactions (tg_id, type, amount, description, created_at) VALUES (?, ?, ?, ?, ?);",
                (
                    referrer_id,
                    "referral_bonus",
                    reward_amount,
                    f"Referral bonus: referee {referee_id} deposit #{deposit_request_id}",
                    now,
                ),
            )
            await db.execute(
                "UPDATE referrals SET rewarded_amount=?, rewarded_at=?, rewarded_deposit_id=? WHERE referee_id=?;",
                (reward_amount, now, deposit_request_id, referee_id),
            )
            await db.commit()
            return {"referrer_id": referrer_id, "referee_id": referee_id, "amount": reward_amount}

    async def list_referrals(self, referrer_id: int, limit: int = 50) -> list[dict]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, referrer_id, referee_id, bonus_amount, created_at FROM referrals WHERE referrer_id=? ORDER BY id DESC LIMIT ?;",
                (referrer_id, int(limit)),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def create_deposit_request(self, tg_id: int, amount: int, method: str, reference: str | None, proof_path: str | None = None) -> int:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                """
                INSERT INTO deposit_requests (tg_id, amount, method, reference, proof_path, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?);
                """,
                (tg_id, int(amount), method, reference, proof_path, now),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def get_deposit_request(self, request_id: int) -> dict | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM deposit_requests WHERE id=?;", (request_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_deposit_proof(self, request_id: int, proof_path: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("UPDATE deposit_requests SET proof_path=? WHERE id=?;", (proof_path, int(request_id)))
            await db.commit()

    async def list_deposit_requests(self, status: str = "pending", limit: int = 20) -> list[dict]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM deposit_requests WHERE status=? ORDER BY id DESC LIMIT ?;",
                (status, int(limit)),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def decide_deposit_request(self, request_id: int, decided_by: int, approve: bool, note: str | None = None) -> dict:
        """
        Approve/decline deposit request. If approved, credits user balance.
        Returns request row after update.
        """
        now = dt.datetime.utcnow().isoformat()
        new_status = "approved" if approve else "declined"
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE;")
            cur = await db.execute("SELECT * FROM deposit_requests WHERE id=?;", (request_id,))
            row = await cur.fetchone()
            if not row:
                await db.execute("ROLLBACK;")
                return {"ok": False, "reason": "not_found"}
            if row["status"] != "pending":
                await db.execute("ROLLBACK;")
                return {"ok": False, "reason": "already_decided"}

            await db.execute(
                """
                UPDATE deposit_requests
                SET status=?, decided_at=?, decided_by=?, note=?
                WHERE id=?;
                """,
                (new_status, now, decided_by, note, request_id),
            )

            if approve:
                amount = int(row["amount"])
                tg_id = int(row["tg_id"])
                await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id=?;", (amount, tg_id))
                await db.execute(
                    "INSERT INTO transactions (tg_id, type, amount, description, created_at) VALUES (?, ?, ?, ?, ?);",
                    (tg_id, "deposit_approved", amount, f"Deposit approved #{request_id} ({row['method']})", now),
                )

            await db.commit()
            cur2 = await db.execute("SELECT * FROM deposit_requests WHERE id=?;", (request_id,))
            row2 = await cur2.fetchone()
            # Referral reward (first approved deposit)
            referral_reward = None
            if approve and row2:
                try:
                    referral_reward = await self.apply_referral_reward_on_deposit(int(row2["tg_id"]), request_id)
                except Exception:
                    referral_reward = None
            return {"ok": True, "request": dict(row2), "referral_reward": referral_reward}

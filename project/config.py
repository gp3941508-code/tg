import os
from dataclasses import dataclass

# dotenv optional (container में नहीं भी हो तो bot चले)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _get_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw.strip())


def _get_env_str(name: str, default: str | None = None) -> str:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        if default is None:
            raise RuntimeError(f"Missing required env var: {name}")
        return default
    return raw.strip()


def _get_env_int_list(name: str) -> list[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return [int(p) for p in parts]


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    admin_ids: set[int]
    db_path: str
    logs_dir: str
    sessions_dir: str
    default_item_price: int
    user_rate_limit_per_sec: float


def load_config() -> Config:
    api_id = _get_env_int("API_ID", 0)
    api_hash = _get_env_str("API_HASH", "")
    bot_token = _get_env_str("BOT_TOKEN", "")

    if not api_id or not api_hash or not bot_token:
        raise RuntimeError("Set API_ID, API_HASH, BOT_TOKEN in environment variables")

    admin_ids = set(_get_env_int_list("ADMIN_IDS"))
    if not admin_ids:
        raise RuntimeError("Set ADMIN_IDS (comma-separated)")

    return Config(
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        admin_ids=admin_ids,
        db_path=os.getenv("DB_PATH", "database.sqlite3"),
        logs_dir=os.getenv("LOGS_DIR", "logs"),
        sessions_dir=os.getenv("SESSIONS_DIR", "sessions"),
        default_item_price=_get_env_int("DEFAULT_ITEM_PRICE", 60),
        user_rate_limit_per_sec=float(os.getenv("USER_RATE_LIMIT_PER_SEC", "1.0")),
    )

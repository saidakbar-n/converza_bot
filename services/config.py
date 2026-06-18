"""Environment helpers shared across the bot service."""

import os
import sys


def is_production() -> bool:
    return os.getenv("ENV", "development").lower() == "production"


def load_local_env_override() -> None:
    """Load repo-root .env.local only outside production."""
    if is_production():
        return
    from pathlib import Path
    from dotenv import load_dotenv

    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env.local", override=True)


def admin_telegram_ids() -> set[str]:
    raw = os.getenv("ADMIN_TELEGRAM_IDS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def is_admin_telegram_id(telegram_id: int | str) -> bool:
    return str(telegram_id) in admin_telegram_ids()


def require_env_vars(names: list[str], service: str = "bot") -> None:
    """Fail fast at startup when required vars are missing in production."""
    if not is_production():
        return
    missing = [name for name in names if not os.getenv(name, "").strip()]
    if missing:
        print(
            f"FATAL [{service}]: missing required env vars in production: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        sys.exit(1)

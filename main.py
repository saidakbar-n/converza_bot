from contextlib import asynccontextmanager
import logging
import os

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from agents.auditor import run_nightly_audit
from routers import webhooks, web_api
from services.config import admin_telegram_ids, is_production, require_env_vars
from services.telegram_bots import (
    APP_BOT_TOKEN,
    SALES_BOT_TOKEN,
    app_api_base,
    sales_api_base,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

APP_COMMANDS = [
    {"command": "start", "description": "Converza onboarding"},
    {"command": "help", "description": "Yordam"},
    {"command": "status", "description": "Obuna va sozlama holati"},
    {"command": "profile", "description": "Brend pasporti"},
    {"command": "subscribe", "description": "Oylik obuna to'lovi"},
    {"command": "report", "description": "Kunlik hisobot"},
    {"command": "fill", "description": "Pasportni to'ldirish"},
]

APP_ADMIN_COMMANDS = APP_COMMANDS + [
    {"command": "admin", "description": "Admin panel"},
    {"command": "pending", "description": "Kutilayotgan arizalar"},
    {"command": "approve", "description": "Arizani tasdiqlash"},
    {"command": "reject", "description": "Arizani rad etish"},
]


async def _set_commands(api_base: str, commands: list, admin_commands: list | None = None) -> None:
    if not api_base:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{api_base}/setMyCommands", json={"commands": commands})
            if admin_commands:
                for admin_id in admin_telegram_ids():
                    try:
                        await client.post(
                            f"{api_base}/setMyCommands",
                            json={
                                "commands": admin_commands,
                                "scope": {"type": "chat", "chat_id": int(admin_id)},
                            },
                        )
                    except Exception as exc:
                        logger.warning("Failed admin commands for %s: %s", admin_id, exc)
    except Exception as exc:
        logger.warning("Failed to set bot commands on %s: %s", api_base, exc)


async def set_bot_commands() -> None:
    await _set_commands(app_api_base(), APP_COMMANDS, APP_ADMIN_COMMANDS)
    # Sales bot has no public DM commands — Business DMs only.


def _validate_startup() -> None:
    require_env_vars(
        [
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
            "HERMES_API_KEY",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_APP_BOT_TOKEN",
            "WEB_APP_URL",
        ],
        service="bot",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_startup()
    await set_bot_commands()

    scheduler = AsyncIOScheduler()
    trigger = CronTrigger(hour=23, minute=59, timezone="Asia/Tashkent")
    scheduler.add_job(run_nightly_audit, trigger)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Converza Telegram Bot", version="0.2.0", lifespan=lifespan)

app.include_router(webhooks.router)
app.include_router(web_api.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "converza_bot"}


@app.get("/ready")
async def ready():
    checks: dict[str, str] = {}
    ok = True

    for key, val in (
        ("SUPABASE_URL", os.getenv("SUPABASE_URL", "")),
        ("HERMES_API_KEY", os.getenv("HERMES_API_KEY", "")),
        ("TELEGRAM_BOT_TOKEN", SALES_BOT_TOKEN),
        ("TELEGRAM_APP_BOT_TOKEN", APP_BOT_TOKEN),
    ):
        if val.strip():
            checks[key] = "ok"
        else:
            checks[key] = "missing"
            ok = False

    try:
        from db.supabase_client import sb
        sb.table("organizations").select("id").limit(1).execute()
        checks["supabase"] = "ok"
    except Exception as exc:
        checks["supabase"] = f"error: {exc}"
        ok = False

    if not ok:
        return {"status": "not_ready", "checks": checks}
    return {"status": "ready", "checks": checks}

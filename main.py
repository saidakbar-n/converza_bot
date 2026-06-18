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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def set_bot_commands() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return

    commands = [
        {"command": "start", "description": "Converza botni boshlash"},
        {"command": "help", "description": "Nimalar qila olishim"},
        {"command": "status", "description": "Bot holatini tekshirish"},
        {"command": "profile", "description": "Brend pasportini ko'rish"},
        {"command": "fill", "description": "Brend pasportini to'ldirish"},
    ]
    if not is_production():
        commands.append(
            {"command": "test_invoice", "description": "Test Click invoice yuborish"}
        )

    admin_commands = commands + [
        {"command": "admin", "description": "Admin panel"},
        {"command": "pending", "description": "Kutilayotgan kirish so'rovlari"},
        {"command": "approve", "description": "So'rovni tasdiqlash"},
        {"command": "reject", "description": "So'rovni rad etish"},
    ]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/setMyCommands",
                json={"commands": commands},
            )
            for admin_id in admin_telegram_ids():
                try:
                    await client.post(
                        f"https://api.telegram.org/bot{token}/setMyCommands",
                        json={
                            "commands": admin_commands,
                            "scope": {
                                "type": "chat",
                                "chat_id": int(admin_id),
                            },
                        },
                    )
                except Exception as exc:
                    logger.warning("Failed to set admin commands for %s: %s", admin_id, exc)
    except Exception as exc:
        logger.warning("Failed to set bot commands: %s", exc)


def _validate_startup() -> None:
    require_env_vars(
        [
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
            "GROQ_API_KEY",
            "TELEGRAM_BOT_TOKEN",
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


app = FastAPI(title="Converza Telegram Bot", version="0.1.0", lifespan=lifespan)

app.include_router(webhooks.router)
app.include_router(web_api.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "converza_bot"}


@app.get("/ready")
async def ready():
    checks: dict[str, str] = {}
    ok = True

    for key in ("SUPABASE_URL", "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN"):
        if os.getenv(key, "").strip():
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

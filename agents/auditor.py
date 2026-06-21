"""Nightly / on-demand daily reports via @ConverzaApp_bot."""

import logging

import httpx

from converza_agent.config import hermes_configured
from db.supabase_client import sb
from services.access_requests import is_user_approved
from services.brand_passport import fetch_passport_by_org
from services.config import is_admin_telegram_id
from services.daily_report import build_daily_report
from services.subscriptions import is_subscription_active
from services.telegram_bots import app_api_base
from services.telegram_send import send_app_message

logger = logging.getLogger(__name__)


def _eligible_org_ids() -> list[str]:
    """Orgs that should receive scheduled reports."""
    rows = sb.table("organizations").select("id").execute().data or []
    eligible: list[str] = []
    for row in rows:
        org_id = str(row.get("id") or "").strip()
        if not org_id.isdigit():
            continue
        passport = fetch_passport_by_org(org_id)
        if not passport or not passport.get("brand_name"):
            continue
        tid = int(org_id)
        if not is_user_approved(tid) and not is_admin_telegram_id(tid):
            continue
        if not is_subscription_active(org_id):
            continue
        eligible.append(org_id)
    return eligible


async def send_daily_report(org_id: str, *, use_hermes: bool = True) -> str:
    """Generate and send report to owner via App bot. Returns report text."""
    text = await build_daily_report(org_id, use_hermes=use_hermes and hermes_configured())
    api = app_api_base()
    if api:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"{api}/sendMessage",
                json={"chat_id": int(org_id), "text": text},
            )
    else:
        await send_app_message(int(org_id), text)
    return text


async def run_nightly_audit() -> None:
    """Cron job — daily report to each active subscribed org (23:59 Asia/Tashkent)."""
    org_ids = _eligible_org_ids()
    if not org_ids:
        logger.info("Nightly audit: no eligible orgs")
        return

    logger.info("Nightly audit: sending to %d org(s)", len(org_ids))
    for org_id in org_ids:
        try:
            await send_daily_report(org_id, use_hermes=True)
        except Exception as exc:
            logger.exception("Daily report failed for org %s: %s", org_id, exc)

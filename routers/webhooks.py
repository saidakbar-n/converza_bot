import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from models.schemas import TelegramUpdate
from agents.ingestor import ingest_message
from agents import hitl
from agents.admin_access import handle_admin_callback
from agents.onboarding import handle_onboarding_message
from agents.business_connection import handle_business_connection
from services.dedup import is_duplicate
from services.subscriptions import activate_subscription
from services.telegram_bots import APP_BOT_USERNAME, app_api_base, sales_api_base
from services.telegram_send import send_app_message, send_sales_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])

HITL_API = f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_HITL_BOT_TOKEN', '')}"
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
WEB_APP_URL = (os.getenv("WEB_APP_URL") or "https://getconverza.com").rstrip("/")


def _verify_webhook_secret(secret_header: str | None) -> None:
    if not WEBHOOK_SECRET:
        return
    if secret_header != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")


async def answer_pre_checkout_query(query: dict, *, api_base: str) -> None:
    query_id = query.get("id")
    if not query_id or not api_base:
        return

    ok = True
    error_message = None
    try:
        total = int(query.get("total_amount", 0))
        currency = (query.get("currency") or "").upper()
        if currency and currency != "UZS":
            ok = False
            error_message = "Faqat UZS valyutasi qo'llab-quvvatlanadi."
        elif total <= 0:
            ok = False
            error_message = "Noto'g'ri to'lov summasi."
    except (TypeError, ValueError):
        ok = False
        error_message = "Noto'g'ri to'lov summasi."

    payload: dict = {"pre_checkout_query_id": query_id, "ok": ok}
    if not ok and error_message:
        payload["error_message"] = error_message

    async with httpx.AsyncClient(timeout=8) as client:
        await client.post(f"{api_base}/answerPreCheckoutQuery", json=payload)


async def handle_sales_successful_payment(update: TelegramUpdate) -> None:
    msg = update.message
    if not msg:
        return
    raw = msg.model_dump(by_alias=True)
    payment = raw.get("successful_payment")
    if not payment:
        return

    from db.supabase_client import sb

    payload = payment.get("invoice_payload", "")
    if payload.startswith("subscription_"):
        logger.warning("Subscription payment on sales bot — ignored payload=%s", payload)
        return

    prospect_id = payload.replace("invoice_", "") if payload.startswith("invoice_") else None
    amount = payment.get("total_amount")
    currency = payment.get("currency")

    logger.info(
        "sales successful_payment prospect=%s amount=%s %s",
        prospect_id,
        amount,
        currency,
    )

    if prospect_id and not prospect_id.startswith("test_"):
        try:
            sb.table("prospects").update({
                "client_condition": "closed",
                "condition_reason": f"To'lov qabul qilindi: {amount} {currency}",
            }).eq("id", prospect_id).execute()
        except Exception as exc:
            logger.warning("Failed to update prospect after payment: %s", exc)


async def handle_app_successful_payment(update: TelegramUpdate) -> None:
    msg = update.message
    if not msg:
        return
    raw = msg.model_dump(by_alias=True)
    payment = raw.get("successful_payment")
    if not payment:
        return

    payload = payment.get("invoice_payload", "")
    amount = payment.get("total_amount")
    charge_id = payment.get("telegram_payment_charge_id")

    if not payload.startswith("subscription_"):
        logger.info("App bot payment with unknown payload: %s", payload)
        return

    org_id = payload.replace("subscription_", "", 1)
    if not org_id:
        return

    activate_subscription(org_id, amount_uzs=amount, charge_id=charge_id)
    await send_app_message(
        msg.chat.id,
        "✅ Converza obunasi faollashtirildi!\n\n"
        f"Endi @{os.getenv('TELEGRAM_BOT_USERNAME', 'ConverzaSales_bot')} ni "
        "Telegram Business → Chatbots orqali ulang.\n"
        f"Boshqaruv: {WEB_APP_URL}",
    )


async def _handle_sales_direct_message(update: TelegramUpdate) -> None:
    msg = update.message
    if not msg or not msg.text:
        return
    if msg.text.startswith("/"):
        return
    await send_sales_message(
        msg.chat.id,
        "Bu bot faqat Telegram Business orqali mijozlar bilan ishlaydi.\n\n"
        f"Ro'yxatdan o'tish: @{APP_BOT_USERNAME}\n"
        f"Veb-sahifa: {WEB_APP_URL}",
    )


def _log_background_task_error(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.exception("Background webhook task failed: %s", exc)


async def _dispatch_sales_update(update: TelegramUpdate) -> None:
    try:
        api = sales_api_base()
        if update.pre_checkout_query:
            await answer_pre_checkout_query(update.pre_checkout_query, api_base=api)
            return

        if update.business_connection:
            await handle_business_connection(update)
            return

        if update.business_message:
            await ingest_message(update)
            return

        if update.message:
            raw = update.message.model_dump(by_alias=True)
            if raw.get("successful_payment"):
                await handle_sales_successful_payment(update)
                return
            await _handle_sales_direct_message(update)
            return
    except Exception:
        logger.exception("Unhandled error dispatching sales update_id=%s", update.update_id)
        raise


async def _dispatch_app_update(update: TelegramUpdate) -> None:
    try:
        api = app_api_base()
        if update.pre_checkout_query:
            await answer_pre_checkout_query(update.pre_checkout_query, api_base=api)
            return

        if update.callback_query:
            await handle_admin_callback(update.callback_query)
            return

        if update.message:
            raw = update.message.model_dump(by_alias=True)
            if raw.get("successful_payment"):
                await handle_app_successful_payment(update)
                return
            await handle_onboarding_message(update)
            return
    except Exception:
        logger.exception("Unhandled error dispatching app update_id=%s", update.update_id)
        raise


@router.post("/telegram")
async def sales_webhook(
    update: TelegramUpdate,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    """@ConverzaSales_bot — Business DMs and end-customer invoices."""
    _verify_webhook_secret(x_telegram_bot_api_secret_token)

    if is_duplicate(update.update_id):
        return {"ok": True}

    task = asyncio.create_task(_dispatch_sales_update(update))
    task.add_done_callback(_log_background_task_error)
    return {"ok": True}


@router.post("/app")
async def app_webhook(
    update: TelegramUpdate,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    """@ConverzaApp_bot — onboarding, admin, Converza subscription."""
    _verify_webhook_secret(x_telegram_bot_api_secret_token)

    if is_duplicate(update.update_id):
        return {"ok": True}

    task = asyncio.create_task(_dispatch_app_update(update))
    task.add_done_callback(_log_background_task_error)
    return {"ok": True}


# ── HITL reviewer webhook ───────────────────────────────────────────────────

def _parse_text_command(text: str) -> tuple[str, str, str | None] | None:
    parts = text.strip().split(maxsplit=2)
    if not parts:
        return None
    cmd = parts[0].lower().lstrip("/")

    if cmd == "approve" and len(parts) >= 2:
        return ("approve", parts[1], None)
    if cmd == "reject" and len(parts) >= 2:
        return ("reject", parts[1], None)
    if cmd == "edit" and len(parts) >= 3:
        return ("edit", parts[1], parts[2])
    return None


def _parse_callback_data(data: str) -> tuple[str, str] | None:
    if ":" not in data:
        return None
    action, draft_id = data.split(":", 1)
    if action in ("approve", "reject", "edit") and draft_id:
        return (action, draft_id)
    return None


async def _answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    url = f"{HITL_API}/answerCallbackQuery"
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json=payload)
    except Exception:
        pass


@router.post("/hitl")
async def hitl_webhook(request: Request):
    update = await request.json()

    cb = update.get("callback_query")
    if cb:
        cb_id = cb.get("id")
        data = cb.get("data") or ""
        reviewer = str((cb.get("from") or {}).get("id", "")) or None

        parsed = _parse_callback_data(data)
        if parsed:
            action, draft_id = parsed
            await hitl.record_decision(
                draft_id=draft_id,
                action=action,
                reviewed_by=reviewer,
            )
            ack = {
                "approve": "Approved ✅",
                "reject": "Rejected ❌",
                "edit": "Send `/edit <id> <text>` to provide the new wording.",
            }.get(action)
            await _answer_callback_query(cb_id, ack)
        else:
            await _answer_callback_query(cb_id, "Unrecognized action.")
        return {"ok": True}

    msg = update.get("message") or {}
    text = msg.get("text") or ""
    reviewer = str((msg.get("from") or {}).get("id", "")) or None

    parsed = _parse_text_command(text)
    if parsed:
        action, draft_id, edited_text = parsed
        await hitl.record_decision(
            draft_id=draft_id,
            action=action,
            edited_text=edited_text,
            reviewed_by=reviewer,
        )

    return {"ok": True}

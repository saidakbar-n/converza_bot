from fastapi import APIRouter, Request
import httpx
import os

from models.schemas import TelegramUpdate
from agents.ingestor import ingest_message
from agents import hitl

router = APIRouter(prefix="/webhook", tags=["webhook"])

HITL_API = f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_HITL_BOT_TOKEN', '')}"


@router.post("/telegram")
async def telegram_webhook(update: TelegramUpdate):
    """Inbound prospect messages from the main brand bot."""
    await ingest_message(update)
    return {"ok": True}


# ── HITL reviewer webhook ───────────────────────────────────────────────────

def _parse_text_command(text: str) -> tuple[str, str, str | None] | None:
    """
    Parse `/approve <id>`, `/reject <id>`, or `/edit <id> <text>`.
    Returns (action, draft_id, edited_text) or None if not a recognized command.
    """
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
    """Parse callback_data of the form `approve:<id>` / `reject:<id>` / `edit:<id>`."""
    if ":" not in data:
        return None
    action, draft_id = data.split(":", 1)
    if action in ("approve", "reject", "edit") and draft_id:
        return (action, draft_id)
    return None


async def _answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    """Acknowledge the callback so Telegram stops the spinner on the button."""
    if not HITL_API.endswith("/"):
        url = f"{HITL_API}/answerCallbackQuery"
    else:
        url = f"{HITL_API}answerCallbackQuery"
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json=payload)
    except Exception:
        pass  # best-effort; never block the webhook on this


@router.post("/hitl")
async def hitl_webhook(request: Request):
    """
    Updates from the reviewer bot. Accepts both:
      * text commands:       /approve <id>, /reject <id>, /edit <id> <text>
      * inline keyboard taps: callback_data = approve:<id> | reject:<id> | edit:<id>
    """
    update = await request.json()

    # ── Inline keyboard tap ─────────────────────────────────────────────────
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
                "reject":  "Rejected ❌",
                "edit":    "Send `/edit <id> <text>` to provide the new wording.",
            }.get(action)
            await _answer_callback_query(cb_id, ack)
        else:
            await _answer_callback_query(cb_id, "Unrecognized action.")

        return {"ok": True}

    # ── Text command ────────────────────────────────────────────────────────
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

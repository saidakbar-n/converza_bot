"""
Admin access-request management via Telegram bot DM.

Commands (ADMIN_TELEGRAM_IDS only):
  /admin    — help + pending count
  /pending  — list pending requests with inline approve/reject
  /view ID  — view one request
  /approve ID [izoh]
  /reject ID [izoh]
"""

import logging
import os
from datetime import datetime

import httpx

from services.access_requests import (
    approve_request,
    find_request_by_prefix,
    get_request,
    list_requests,
    pending_count,
    reject_request,
)
from services.config import is_admin_telegram_id
from services.telegram_send import send_message

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"

_ADMIN_COMMANDS = (
    "/admin",
    "/pending",
    "/view",
    "/approve",
    "/reject",
    "/approved",
    "/rejected",
)


def is_admin_command(text: str) -> bool:
    if not text:
        return False
    cmd = text.strip().split()[0].lower().split("@")[0]
    return cmd in _ADMIN_COMMANDS


def _short_id(request_id: str) -> str:
    return str(request_id).split("-")[0]


def _format_sent_at(value: str | None) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def _format_request(row: dict) -> str:
    """Full card for admin review — business name, description, phone, sent date."""
    lines = [
        f"🏢 {row.get('business_name', '—')}",
        f"👤 {row.get('full_name', '—')}",
        f"☎️ {row.get('contact', '—')}",
        f"📱 @{row.get('telegram_username') or '—'}",
        f"🕐 Yuborilgan: {_format_sent_at(row.get('created_at'))}",
        f"📌 Holat: {row.get('status', '—')}",
        f"🆔 ID: {_short_id(row['id'])}",
        "",
        "📝 Muammo / og'riq nuqtasi:",
        (row.get("message") or "—").strip(),
    ]
    if row.get("review_note"):
        lines += ["", f"Admin izohi: {row['review_note']}"]
    return "\n".join(lines)


async def _answer_callback(callback_query_id: str, text: str | None = None) -> None:
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(f"{TELEGRAM_API}/answerCallbackQuery", json=payload)
    except Exception as exc:
        logger.warning("answerCallbackQuery failed: %s", exc)


async def _send_with_keyboard(chat_id: int, text: str, request_id: str) -> None:
    short = _short_id(request_id)
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "reply_markup": {
                    "inline_keyboard": [[
                        {"text": "✅ Tasdiqlash", "callback_data": f"access_approve:{short}"},
                        {"text": "❌ Rad etish", "callback_data": f"access_reject:{short}"},
                    ]]
                },
            },
        )


async def handle_admin_callback(callback: dict) -> None:
    """Inline keyboard taps from /pending listings."""
    cb_id = callback.get("id")
    data = callback.get("data") or ""
    sender = callback.get("from") or {}
    chat = callback.get("message", {}).get("chat", {})
    chat_id = chat.get("id")
    sender_id = sender.get("id")

    if not chat_id or not sender_id:
        return

    if not is_admin_telegram_id(sender_id):
        await _answer_callback(cb_id, "Admin huquqi yo'q.")
        return

    if not data.startswith("access_"):
        await _answer_callback(cb_id)
        return

    try:
        action, short = data.split(":", 1)
        row = find_request_by_prefix(short, status="pending")
        if action == "access_approve":
            approved = approve_request(row["id"])
            await _answer_callback(cb_id, "Tasdiqlandi ✅")
            await send_message(
                int(chat_id),
                f"✅ Tasdiqlandi\n\n{_format_request(approved)}",
            )
        elif action == "access_reject":
            rejected = reject_request(row["id"])
            await _answer_callback(cb_id, "Rad etildi ❌")
            await send_message(
                int(chat_id),
                f"❌ Rad etildi\n\n{_format_request(rejected)}",
            )
        else:
            await _answer_callback(cb_id)
    except ValueError as exc:
        await _answer_callback(cb_id, str(exc)[:180])
    except Exception as exc:
        logger.exception("admin callback failed")
        await _answer_callback(cb_id, f"Xatolik: {exc}"[:180])


async def handle_admin_command(chat_id: int, sender_id: int, text: str) -> None:
    if not is_admin_telegram_id(sender_id):
        await send_message(chat_id, "Bu buyruq faqat adminlar uchun.")
        return

    parts = text.strip().split(maxsplit=2)
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1] if len(parts) > 1 else ""
    note = parts[2] if len(parts) > 2 else ""

    try:
        if cmd == "/admin":
            count = pending_count()
            await send_message(
                chat_id,
                "🛠 Admin panel\n\n"
                f"Kutilayotgan so'rovlar: {count}\n\n"
                "/pending — biznes nomi, muammo, telefon bilan ro'yxat\n"
                "/view ID — bitta so'rov\n"
                "/approve ID — tasdiqlash\n"
                "/reject ID sabab — rad etish\n"
                "/approved — oxirgi tasdiqlanganlar\n"
                "/rejected — oxirgi rad etilganlar",
            )
            return

        if cmd == "/pending":
            rows = list_requests("pending", limit=10)
            if not rows:
                await send_message(chat_id, "Kutilayotgan so'rovlar yo'q.")
                return
            await send_message(chat_id, f"📋 Kutilayotgan so'rovlar ({len(rows)}):")
            for row in rows:
                await _send_with_keyboard(chat_id, _format_request(row), row["id"])
            return

        if cmd == "/approved":
            rows = list_requests("approved", limit=10)
            if not rows:
                await send_message(chat_id, "Tasdiqlangan so'rovlar yo'q.")
                return
            for row in rows:
                await send_message(chat_id, _format_request(row))
            return

        if cmd == "/rejected":
            rows = list_requests("rejected", limit=10)
            if not rows:
                await send_message(chat_id, "Rad etilgan so'rovlar yo'q.")
                return
            for row in rows:
                await send_message(chat_id, _format_request(row))
            return

        if cmd == "/view":
            if not arg:
                await send_message(chat_id, "Foydalanish: /view <ID>")
                return
            row = find_request_by_prefix(arg)
            await send_message(chat_id, _format_request(row))
            if row.get("status") == "pending":
                await _send_with_keyboard(chat_id, "Harakat tanlang:", row["id"])
            return

        if cmd == "/approve":
            if not arg:
                await send_message(chat_id, "Foydalanish: /approve <ID> [izoh]")
                return
            row = find_request_by_prefix(arg, status="pending")
            approved = approve_request(row["id"], note)
            await send_message(chat_id, f"✅ Tasdiqlandi\n\n{_format_request(approved)}")
            return

        if cmd == "/reject":
            if not arg:
                await send_message(chat_id, "Foydalanish: /reject <ID> [sabab]")
                return
            row = find_request_by_prefix(arg, status="pending")
            rejected = reject_request(row["id"], note)
            await send_message(chat_id, f"❌ Rad etildi\n\n{_format_request(rejected)}")
            return

    except ValueError as exc:
        await send_message(chat_id, str(exc))
    except Exception as exc:
        logger.exception("admin command failed: %s", cmd)
        await send_message(chat_id, f"Xatolik: {exc}")

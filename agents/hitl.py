"""
HITL (Human-in-the-Loop) — sends draft replies to a human reviewer via a
separate Telegram bot and waits for approval before the closer sends.

Flow:
  1. Closer calls request_approval() with the draft reply.
  2. save_draft() persists the draft to the `drafts` table (status='pending').
  3. HITL bot posts the draft + inline keyboard buttons to the reviewer chat.
     Buttons carry callback_data: "approve:<id>" / "reject:<id>" / "edit:<id>"
  4. Reviewer clicks a button OR sends `/approve <id>` | `/reject <id>` |
     `/edit <id> <text>`. The /webhook/hitl route parses the update and calls
     record_decision(), which updates the draft row.
  5. request_approval() polls the draft row until status changes from 'pending'
     (or the timeout elapses → status='timeout', auto-reject).
"""

import os
import asyncio
from datetime import datetime, timezone
import httpx
from db.supabase_client import sb
from models.schemas import HITLDecision, DraftCreate

TELEGRAM_HITL_BOT_TOKEN = os.environ.get("TELEGRAM_HITL_BOT_TOKEN", "")
TELEGRAM_HITL_CHAT_ID = os.environ.get("TELEGRAM_HITL_CHAT_ID", "")
HITL_API = f"https://api.telegram.org/bot{TELEGRAM_HITL_BOT_TOKEN}"

APPROVAL_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 2


# ── Drafts table helpers ─────────────────────────────────────────────────────

async def save_draft(
    org_id: str,
    prospect_id: str | None,
    conversation_id: str,
    draft_content: str,
    context_summary: str | None,
) -> str:
    """Insert a pending draft row and return its id."""
    payload = DraftCreate(
        org_id=org_id,
        prospect_id=prospect_id,
        conversation_id=conversation_id,
        draft_content=draft_content,
        context_summary=context_summary,
        status="pending",
    ).model_dump()

    result = sb.table("drafts").insert(payload).execute()
    return result.data[0]["id"]


async def get_draft(draft_id: str) -> dict | None:
    """Return the draft row, or None if it doesn't exist."""
    result = (
        sb.table("drafts")
        .select("*")
        .eq("id", draft_id)
        .maybe_single()
        .execute()
    )
    return result.data if result else None


async def update_draft(
    draft_id: str,
    status: str,
    final_content: str | None = None,
    reviewed_by: str | None = None,
) -> None:
    """Update a draft's status and optionally final_content / reviewer."""
    patch: dict = {
        "status": status,
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    if final_content is not None:
        patch["final_content"] = final_content
    if reviewed_by is not None:
        patch["reviewed_by"] = reviewed_by

    sb.table("drafts").update(patch).eq("id", draft_id).execute()


# ── Public API ───────────────────────────────────────────────────────────────

async def request_approval(
    org_id: str,
    prospect_id: str,
    conversation_id: str,
    draft_reply: str,
    context_summary: str,
) -> HITLDecision:
    """
    Save the draft, post it to the reviewer chat with inline buttons,
    then poll the drafts table until a decision is made or we time out.
    """
    # 1. Persist the draft
    draft_id = await save_draft(
        org_id=org_id,
        prospect_id=prospect_id,
        conversation_id=conversation_id,
        draft_content=draft_reply,
        context_summary=context_summary,
    )

    # 2. Post to reviewer chat with inline keyboard
    text = (
        f"📨 *New draft pending approval*\n\n"
        f"*Draft id:* `{draft_id}`\n"
        f"*Conversation:* `{conversation_id}`\n\n"
        f"*Inbound context:*\n_{context_summary}_\n\n"
        f"*Draft reply:*\n{draft_reply}\n\n"
        f"Tap a button below, or send:\n"
        f"  `/approve {draft_id}`\n"
        f"  `/reject {draft_id}`\n"
        f"  `/edit {draft_id} <new text>`"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve:{draft_id}"},
                {"text": "❌ Reject",  "callback_data": f"reject:{draft_id}"},
                {"text": "✏️ Edit",   "callback_data": f"edit:{draft_id}"},
            ]
        ]
    }

    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{HITL_API}/sendMessage",
            json={
                "chat_id": TELEGRAM_HITL_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup,
            },
        )

    # 3. Poll for decision
    elapsed = 0
    while elapsed < APPROVAL_TIMEOUT_SECONDS:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS

        draft = await get_draft(draft_id)
        if not draft or draft["status"] == "pending":
            continue

        status = draft["status"]
        approved = status in ("approved", "edited")
        edited_reply = draft.get("final_content") if status == "edited" else None
        return HITLDecision(
            conversation_id=conversation_id,
            approved=approved,
            edited_reply=edited_reply,
        )

    # 4. Timeout — mark the draft and auto-reject
    await update_draft(draft_id, status="timeout")
    return HITLDecision(conversation_id=conversation_id, approved=False)


async def record_decision(
    draft_id: str,
    action: str,
    edited_text: str | None = None,
    reviewed_by: str | None = None,
) -> None:
    """
    Called by the /webhook/hitl route when the reviewer responds.
    `action` is one of: approve | reject | edit
    """
    if action == "approve":
        await update_draft(draft_id, status="approved", reviewed_by=reviewed_by)
    elif action == "reject":
        await update_draft(draft_id, status="rejected", reviewed_by=reviewed_by)
    elif action == "edit":
        # An edit action without text is a no-op (the reviewer must follow up
        # with `/edit <id> <text>`); the drafts row stays pending.
        if edited_text:
            await update_draft(
                draft_id,
                status="edited",
                final_content=edited_text,
                reviewed_by=reviewed_by,
            )

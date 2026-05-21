"""
Ingestor — entry point for every inbound Telegram update.

Responsibilities:
1. Extract sender identity and message text from the Telegram update.
2. Upsert the prospect into Supabase (idempotent by external_id).
3. Ensure the prospect has a stable conversation_id (create on first contact).
4. Log the inbound message to the messages table.
5. Hand off to the Closer agent to generate a reply.
"""

import os
import uuid
from models.schemas import TelegramUpdate, ProspectCreate, MessageCreate
from db.supabase_client import sb
from agents.closer import generate_reply

# Default org — replace with telegram_connections lookup when multi-tenant
DEFAULT_ORG_ID = os.getenv("DEFAULT_ORG_ID", "")


async def ingest_message(update: TelegramUpdate) -> None:
    msg = update.message
    if not msg or not msg.text:
        return  # ignore non-text updates (stickers, media, etc.)

    sender = msg.from_
    if not sender or sender.is_bot:
        return

    # ── 1. Upsert prospect ──────────────────────────────────────────────────
    prospect_data = ProspectCreate(
        org_id=DEFAULT_ORG_ID,
        platform="telegram",
        external_id=str(sender.id),
        metadata={
            "first_name": sender.first_name,
            "username": sender.username,
            "language_code": sender.language_code,
        },
    )

    upsert_result = (
        sb.table("prospects")
        .upsert(
            prospect_data.model_dump(),
            on_conflict="org_id,platform,external_id",
        )
        .execute()
    )

    prospect_id: str | None = None
    conversation_id: str | None = None
    if upsert_result.data:
        prospect_id = upsert_result.data[0]["id"]
        conversation_id = upsert_result.data[0].get("conversation_id")

    # ── 2. Ensure conversation_id exists on the prospect ────────────────────
    if prospect_id and not conversation_id:
        conversation_id = str(uuid.uuid4())
        sb.table("prospects").update(
            {"conversation_id": conversation_id}
        ).eq("id", prospect_id).execute()

    # ── 3. Log inbound message ──────────────────────────────────────────────
    inbound = MessageCreate(
        org_id=DEFAULT_ORG_ID,
        prospect_id=prospect_id,
        direction="inbound",
        content=msg.text,
        sent_by="system",
        conversation_id=conversation_id,
    )
    sb.table("messages").insert(inbound.model_dump()).execute()

    # ── 4. Hand off to closer ───────────────────────────────────────────────
    if prospect_id and conversation_id:
        await generate_reply(
            chat_id=msg.chat.id,
            prospect_id=prospect_id,
            inbound_text=msg.text,
            org_id=DEFAULT_ORG_ID,
            conversation_id=conversation_id,
        )

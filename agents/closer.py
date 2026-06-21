"""
Closer — thin Hermes dispatch for DM sales.

Reasoning + MCP tool calls run inside Hermes. Python handles HITL and Telegram delivery.
"""

import json
import os

import httpx
from db.supabase_client import sb
from agents.hitl import request_approval
from agents.searcher import get_organization
from models.schemas import MessageCreate
from services.org_resolver import lookup_business_connection_id
from services.payments import (
    get_payment_provider_token,
    is_configured_provider_token,
    payment_unavailable_prospect_message,
)
from converza_agent.config import hermes_model
from converza_agent.runtime import run_agent_json

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
HITL_ENABLED = os.getenv("HITL_ENABLED", "false").lower() == "true"

# Re-export for onboarding invoice tests
from converza_mcp.telegram_actions import select_invoice_item, send_invoice  # noqa: E402


def _telegram_send_payload(
    chat_id: int,
    text: str,
    business_connection_id: str | None = None,
) -> dict:
    payload: dict = {"chat_id": chat_id, "text": text}
    if business_connection_id:
        payload["business_connection_id"] = business_connection_id
    return payload


async def _resolve_business_connection_id(
    org_id: str,
    business_connection_id: str | None,
) -> str | None:
    if business_connection_id:
        return business_connection_id
    return lookup_business_connection_id(org_id)


async def generate_reply(
    chat_id: int,
    prospect_id: str,
    inbound_text: str,
    org_id: str,
    conversation_id: str,
    business_connection_id: str | None = None,
) -> None:
    org = await get_organization(org_id)
    brand = org.get("brand_context", {})
    click_token = get_payment_provider_token(org)
    conn_id = await _resolve_business_connection_id(org_id, business_connection_id)
    session_key = f"converza:closer:{org_id}:{prospect_id}"

    payload = {
        "org_id": org_id,
        "prospect_id": prospect_id,
        "chat_id": chat_id,
        "inbound_text": inbound_text,
    }

    try:
        draft_json = await run_agent_json(
            "dm-closer",
            [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            session_key=session_key,
            max_tokens=600,
        )
        draft = draft_json.get("reply", "").strip()
        condition = draft_json.get("client_condition", "cold")
        reason = draft_json.get("condition_reason", "")
        invoice_required = bool(draft_json.get("invoice_required"))
        invoice_tier = draft_json.get("invoice_tier")
    except Exception:
        draft = "Kechirasiz, men hozir javob bera olmayman."
        condition = "cold"
        reason = "hermes_error"
        invoice_required = False
        invoice_tier = None

    sb.table("prospects").update(
        {"client_condition": condition, "condition_reason": reason}
    ).eq("id", prospect_id).execute()

    approved_reply = draft
    if HITL_ENABLED:
        decision = await request_approval(
            org_id=org_id,
            prospect_id=prospect_id,
            conversation_id=conversation_id,
            draft_reply=draft,
            context_summary=inbound_text[:200],
        )
        if not decision.approved:
            return
        if decision.edited_reply:
            approved_reply = decision.edited_reply

    is_invoice = invoice_required or "[TRIGGER_INVOICE]" in approved_reply
    final_text = approved_reply.replace("[TRIGGER_INVOICE]", "").strip()

    if is_invoice and is_configured_provider_token(click_token):
        if final_text:
            async with httpx.AsyncClient(timeout=10) as client:
                msg_resp = await client.post(
                    f"{TELEGRAM_API}/sendMessage",
                    json=_telegram_send_payload(chat_id, final_text, conn_id),
                )
                msg_resp.raise_for_status()
        invoice_item = select_invoice_item(brand, invoice_tier)
        tg_resp = await send_invoice(
            chat_id, click_token, prospect_id, invoice_item, conn_id
        )
        tg_resp.raise_for_status()
    else:
        if is_invoice and not is_configured_provider_token(click_token):
            if not final_text:
                final_text = payment_unavailable_prospect_message()
        async with httpx.AsyncClient(timeout=10) as client:
            tg_resp = await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json=_telegram_send_payload(chat_id, final_text, conn_id),
            )
        tg_resp.raise_for_status()

    outbound = MessageCreate(
        org_id=org_id,
        prospect_id=prospect_id,
        direction="outbound",
        content=approved_reply,
        sent_by="ai",
        agent_model=hermes_model(),
        conversation_id=conversation_id,
    )
    sb.table("messages").insert(outbound.model_dump()).execute()

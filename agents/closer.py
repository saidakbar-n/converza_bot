"""
Closer — the core sales agent.

Uses Groq (llama-3.3-70b-versatile) to generate a contextual reply
grounded in the brand passport, pricing, FAQ, and conversation history.

Flow:
  1. Searcher fetches brand context + conversation history.
  2. Build a system prompt from the brand passport.
  3. Call Groq chat completions.
  4. Route reply through HITL if enabled.
  5. Send approved reply via Telegram and log it to Supabase.
"""

import os
import httpx
from db.supabase_client import sb
from agents.searcher import get_brand_context, get_conversation_history
from agents.hitl import request_approval
from models.schemas import MessageCreate

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL = "llama-3.3-70b-versatile"
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

HITL_ENABLED = os.getenv("HITL_ENABLED", "false").lower() == "true"


def _build_system_prompt(brand: dict) -> str:
    faq_text = ""
    for item in brand.get("faq", []):
        faq_text += f"Q: {item.get('question', '')}\nA: {item.get('answer', '')}\n"

    pricing_text = ""
    for tier in brand.get("pricing", []):
        features = ", ".join(tier.get("features", []))
        pricing_text += f"- {tier.get('tier')}: {tier.get('price')} — {features}\n"

    return f"""You are a warm, confident DM closer for {brand.get('brand_name', 'this brand')}.

BRAND CONTEXT
Industry: {brand.get('industry', 'N/A')}
Core offer: {brand.get('core_offer', 'N/A')}
Target audience: {brand.get('target_audience', 'N/A')}
USP: {brand.get('usp', 'N/A')}

PRICING
{pricing_text or 'Not specified.'}

FAQ
{faq_text or 'Not specified.'}

RULES
- Be conversational, never robotic. Match the prospect's energy.
- Ask ONE qualifying question at a time. Never interrogate.
- When the prospect shows buying intent, present the most relevant offer.
- Handle objections with empathy then pivot back to value.
- Never lie, never pressure. If you don't know something, say so honestly.
- Keep replies concise — 1–3 sentences max unless explaining pricing or FAQ.
- Do NOT use emojis unless the prospect uses them first."""


async def generate_reply(
    chat_id: int,
    prospect_id: str,
    inbound_text: str,
    org_id: str,
    conversation_id: str,
) -> None:
    # ── 1. Fetch context ────────────────────────────────────────────────────
    brand = await get_brand_context(org_id)
    history = await get_conversation_history(org_id, prospect_id)

    # ── 2. Build messages ───────────────────────────────────────────────────
    system_prompt = _build_system_prompt(brand)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": inbound_text})

    # ── 3. Call Groq ────────────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={"model": GROQ_MODEL, "messages": messages, "max_tokens": 300},
        )
        resp.raise_for_status()

    draft = resp.json()["choices"][0]["message"]["content"].strip()

    # ── 4. HITL gate ─────────────────────────────────────────────────────────
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
            return  # human rejected — do not send
        if decision.edited_reply:
            approved_reply = decision.edited_reply

    # ── 5. Send via Telegram ─────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=10) as client:
        tg_resp = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": approved_reply},
        )
        tg_resp.raise_for_status()

    # ── 6. Log outbound message ──────────────────────────────────────────────
    outbound = MessageCreate(
        org_id=org_id,
        prospect_id=prospect_id,
        direction="outbound",
        content=approved_reply,
        sent_by="ai",
        agent_model=GROQ_MODEL,
        conversation_id=conversation_id,
    )
    sb.table("messages").insert(outbound.model_dump()).execute()

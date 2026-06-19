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
import re
import httpx
from db.supabase_client import sb
from agents.searcher import get_organization, get_conversation_history
from agents.hitl import request_approval
from models.schemas import MessageCreate
from services.org_resolver import lookup_business_connection_id
from services.payments import (
    get_payment_provider_token,
    is_configured_provider_token,
    payment_unavailable_prospect_message,
    payments_enabled,
)

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL = "llama-3.3-70b-versatile"
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

HITL_ENABLED = os.getenv("HITL_ENABLED", "false").lower() == "true"
DEFAULT_USD_TO_UZS = int(os.getenv("USD_TO_UZS", "12500"))
# Telegram UZS invoices use whole so'm (no subunits). Default ≈ 375 000 so'm.
DEFAULT_INVOICE_AMOUNT_UZS = int(os.getenv("DEFAULT_INVOICE_AMOUNT_UZS", "375000"))
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


def _price_to_uzs(price: object) -> int | None:
    """Parse a brand-passport price into whole Uzbek so'm for Telegram UZS invoices."""
    if price is None:
        return None

    if isinstance(price, (int, float)):
        val = float(price)
        # Structured JSON prices are already whole so'm (e.g. 99000).
        return int(round(val))

    text = str(price).strip()
    if not text:
        return None

    lowered = text.lower()
    is_usd = "$" in text or "usd" in lowered

    # "99 000 so'm/oy", "99'000", "99,000" → 99000
    digits_only = re.sub(r"[^\d.]", "", text.replace(" ", "").replace("'", "").replace("’", ""))
    if not digits_only:
        return None

    try:
        amount = float(digits_only)
    except ValueError:
        return None

    if is_usd:
        amount *= DEFAULT_USD_TO_UZS

    amount_int = int(round(amount))
    return amount_int if amount_int > 0 else None


def select_invoice_item(brand: dict, requested_tier: str | None = None) -> dict:
    pricing = brand.get("pricing") or []
    selected = None
    if requested_tier:
        requested = requested_tier.lower()
        selected = next(
            (
                item for item in pricing
                if requested in str(item.get("tier", "")).lower()
                or requested in str(item.get("name", "")).lower()
            ),
            None,
        )

    if not selected and pricing:
        selected = pricing[0]

    selected = selected or {}
    tier = selected.get("tier") or selected.get("name") or "DM Closer"
    price = selected.get("price") or selected.get("amount")
    amount = _price_to_uzs(price) or DEFAULT_INVOICE_AMOUNT_UZS
    features = selected.get("features") or []
    description = ", ".join(features[:3]) if features else brand.get("core_offer", "Telegram DM Closer")

    return {
        "title": f"{brand.get('brand_name', 'Converza')} {tier}",
        "description": description[:255],
        "label": str(tier),
        "amount": amount,
    }


async def send_invoice(
    chat_id: int,
    provider_token: str,
    payload_id: str,
    invoice_item: dict | None = None,
    business_connection_id: str | None = None,
) -> httpx.Response:
    item = invoice_item or {
        "title": "Converza DM Closer",
        "description": "Telegram orqali to'lov",
        "label": "DM Closer",
        "amount": DEFAULT_INVOICE_AMOUNT_UZS,
    }

    body: dict = {
        "chat_id": chat_id,
        "title": item["title"][:32],
        "description": item["description"][:255],
        "payload": f"invoice_{payload_id}",
        "provider_token": provider_token,
        "currency": "UZS",
        "prices": [{"label": item["label"][:32], "amount": item["amount"]}],
    }
    if business_connection_id:
        body["business_connection_id"] = business_connection_id

    async with httpx.AsyncClient(timeout=10) as client:
        return await client.post(f"{TELEGRAM_API}/sendInvoice", json=body)


def _build_system_prompt(brand: dict, *, payments_enabled: bool) -> str:
    faq_text = ""
    for item in brand.get("faq", []):
        faq_text += f"Q: {item.get('question', '')}\nA: {item.get('answer', '')}\n"

    pricing_text = ""
    for tier in brand.get("pricing", []):
        features = ", ".join(tier.get("features", []))
        pricing_text += f"- {tier.get('tier')}: {tier.get('price')} — {features}\n"

    objections_text = ""
    for item in brand.get("objections", []):
        objections_text += (
            f"- {item.get('objection', '')}: {item.get('response', '')}\n"
        )

    raw_notes = brand.get("raw_notes") or ""
    tone = brand.get("tone") or brand.get("brand_voice") or "samimiy va ishonchli"

    if payments_enabled:
        payment_rule = (
            "- Agar mijoz sotib olishga rozi bo'lsa yoki to'lov qilmoqchi bo'lsa, "
            "invoice_required=true qiling va mos pricing tier nomini invoice_tier ga yozing."
        )
    else:
        payment_rule = (
            "- Click to'lovi hozir yoqilmagan. invoice_required hech qachon true bo'lmasin; "
            "to'lov bo'yicha mijozga qisqa yo'riqnoma yoki bog'lanish taklif qiling."
        )

    return f"""Siz {brand.get('brand_name', 'ushbu kompaniya')} uchun juda samimiy va ishonchli sotuv menejerisiz. Barcha javoblaringiz faqat O'zbek tilida bo'lishi shart.

Javobingizni har doim qat'iy JSON formatida qaytarishingiz shart. JSON strukturasi quyidagicha bo'lishi kerak:
{{
  "reply": "Sizning O'zbek tilidagi javob matningiz...",
  "client_condition": "cold | warm | purchasing | closed",
  "condition_reason": "Mijozning holati nima uchun shunday baholanganligi haqida qisqacha izoh.",
  "invoice_required": false,
  "invoice_tier": "pricing ichidagi tier nomi yoki null"
}}

Kompaniya haqida:
Soha: {brand.get('industry', 'N/A')}
Asosiy taklif: {brand.get('core_offer', 'N/A')}
Maqsadli auditoriya: {brand.get('target_audience', 'N/A')}
Muloqot ohangi: {tone}

Narxlar:
{pricing_text or 'N/A'}

FAQ:
{faq_text or 'N/A'}

E'tirozlar va javoblar:
{objections_text or 'N/A'}

Qo'shimcha qoidalar:
{raw_notes or 'N/A'}

QOIDALAR:
- O'zbek tilida, tabiiy va samimiy gapiring. Hech qachon robotdek gapirmang.
- Bir vaqtning o'zida faqat Bitta savol bering. Tergov qilmang.
- Mijozning e'tirozlarini to'g'ri qabul qilib, unga qiymatni tushuntiring.
{payment_rule}
- Iloji boricha qisqa (1-3 gap) va lo'nda yozing.
- Mijoz birinchi bo'lib emoji ishlatmaguncha emoji ishlatmang.
- FAQAT JSON formatida javob qaytaring, boshqa hech qanday so'z yozmang."""


async def generate_reply(
    chat_id: int,
    prospect_id: str,
    inbound_text: str,
    org_id: str,
    conversation_id: str,
    business_connection_id: str | None = None,
) -> None:
    # ── 1. Fetch context ────────────────────────────────────────────────────
    org = await get_organization(org_id)
    brand = org.get("brand_context", {})
    click_token = get_payment_provider_token(org)
    history = await get_conversation_history(org_id, prospect_id)
    conn_id = await _resolve_business_connection_id(org_id, business_connection_id)

    # ── 2. Build messages ───────────────────────────────────────────────────
    can_accept_payments = payments_enabled(org)
    system_prompt = _build_system_prompt(brand, payments_enabled=can_accept_payments)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": inbound_text})

    # ── 3. Call Groq ────────────────────────────────────────────────────────
    import json
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": GROQ_MODEL, 
                "messages": messages, 
                "max_tokens": 500,
                "response_format": {"type": "json_object"}
            },
        )
        resp.raise_for_status()

    try:
        draft_json = json.loads(resp.json()["choices"][0]["message"]["content"])
        draft = draft_json.get("reply", "").strip()
        condition = draft_json.get("client_condition", "cold")
        reason = draft_json.get("condition_reason", "")
        invoice_required = bool(draft_json.get("invoice_required"))
        invoice_tier = draft_json.get("invoice_tier")
    except Exception:
        draft = "Kechirasiz, men hozir javob bera olmayman."
        condition = "cold"
        reason = "Error parsing JSON"
        invoice_required = False
        invoice_tier = None

    # Update prospect condition in DB
    sb.table("prospects").update({
        "client_condition": condition,
        "condition_reason": reason
    }).eq("id", prospect_id).execute()

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

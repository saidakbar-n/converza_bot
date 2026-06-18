import json
import logging
import fitz  # PyMuPDF
import httpx
import os

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"

# Plain-text (string) passport fields that the merge logic treats as scalars.
_SCALAR_FIELDS = (
    "brand_name",
    "industry",
    "target_location",
    "target_audience",
    "core_offer",
    "tone",
    "brand_voice",
)
# List-of-objects passport fields that the merge logic concatenates.
_LIST_FIELDS = ("pricing", "faq", "objections")

PASSPORT_SCHEMA = {
    "brand_name": "string",
    "industry": "string",
    "target_location": "string",
    "target_audience": "string",
    "core_offer": "string",
    "tone": "string",
    "brand_voice": "string",
    "pricing": [{"tier": "string", "price": "string", "features": ["string"]}],
    "faq": [{"question": "string", "answer": "string"}],
    "objections": [{"objection": "string", "response": "string"}],
    "raw_notes": "string",
}


async def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from PDF using PyMuPDF."""
    text = ""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in doc:
            text += page.get_text()
    finally:
        doc.close()
    return text


async def summarize_to_structured_passport(raw_text: str) -> dict:
    """Uses Groq to extract structured brand passport JSON from raw document text."""
    system_prompt = (
        "Siz biznes ma'lumotlarini tahlil qiluvchi AI tizimisiz.\n"
        "Berilgan matndan strukturalangan Brand Passport JSON yarating.\n"
        "Barcha matn maydonlari o'zbek tilida bo'lsin.\n"
        "Faqat quyidagi JSON strukturasini qaytaring, boshqa hech narsa yozmang:\n"
        f"{json.dumps(PASSPORT_SCHEMA, ensure_ascii=False)}\n\n"
        "pricing, faq, objections bo'sh bo'lsa [] qaytaring."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"Quyidagi matndan kompaniya pasportini yarating:\n\n{raw_text[:12000]}",
        },
    ]

    if not GROQ_API_KEY:
        # No LLM available — degrade gracefully instead of failing silently.
        logger.warning("GROQ_API_KEY is not set; storing raw text only.")
        return {"raw_notes": raw_text[:4000]}

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": GROQ_MODEL,
                "messages": messages,
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"].strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"raw_notes": content}

    return parsed


async def process_document(pdf_bytes: bytes) -> dict:
    """Main pipeline: extract PDF text -> structured brand passport JSON."""
    text = await extract_text_from_pdf(pdf_bytes)
    if not text.strip():
        return {
            "brand_name": "",
            "raw_notes": "Matn topilmadi.",
        }
    return await summarize_to_structured_passport(text)


async def process_freeform_text(raw_text: str) -> dict:
    """Structure a free-text business description into a brand passport JSON."""
    if not raw_text.strip():
        return {"brand_name": "", "raw_notes": ""}
    return await summarize_to_structured_passport(raw_text)


def merge_passports(base: dict | None, new: dict | None) -> dict:
    """
    Merge a freshly-parsed passport (`new`) onto an existing one (`base`).

    - Scalar text fields: keep the existing value unless empty, then take new.
      A non-empty new value overrides an empty/placeholder existing one.
    - List fields (pricing/faq/objections): concatenate while de-duplicating.
    - raw_notes: append so multiple PDFs accumulate context.
    """
    base = dict(base or {})
    new = dict(new or {})
    merged = dict(base)

    for field in _SCALAR_FIELDS:
        new_val = (new.get(field) or "").strip() if isinstance(new.get(field), str) else new.get(field)
        old_val = (base.get(field) or "").strip() if isinstance(base.get(field), str) else base.get(field)
        # New non-empty value wins; otherwise keep the old one.
        merged[field] = new_val or old_val or old_val

    for field in _LIST_FIELDS:
        combined = list(base.get(field) or [])
        seen = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in combined}
        for item in (new.get(field) or []):
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                combined.append(item)
                seen.add(key)
        merged[field] = combined

    old_notes = (base.get("raw_notes") or "").strip()
    new_notes = (new.get("raw_notes") or "").strip()
    if new_notes and new_notes != old_notes:
        merged["raw_notes"] = (old_notes + "\n\n" + new_notes).strip() if old_notes else new_notes
    else:
        merged["raw_notes"] = old_notes or new_notes

    return merged

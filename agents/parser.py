import json
import logging

import fitz  # PyMuPDF

from converza_agent.config import hermes_configured
from converza_agent.runtime import run_agent_json

logger = logging.getLogger(__name__)

_SCALAR_FIELDS = (
    "brand_name",
    "industry",
    "target_location",
    "target_audience",
    "core_offer",
    "tone",
    "brand_voice",
)
_LIST_FIELDS = ("pricing", "faq", "objections")


async def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    text = ""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in doc:
            text += page.get_text()
    finally:
        doc.close()
    return text


async def summarize_to_structured_passport(raw_text: str) -> dict:
    if not hermes_configured():
        logger.warning("Hermes not configured; storing raw text only.")
        return {"raw_notes": raw_text[:4000]}

    try:
        return await run_agent_json(
            "passport-extract",
            [
                {
                    "role": "user",
                    "content": f"Hujjat matni:\n\n{raw_text[:12000]}",
                }
            ],
            session_key="converza:parser",
            max_tokens=2000,
            temperature=0.2,
        )
    except Exception as exc:
        logger.warning("Passport extraction failed: %s", exc)
        return {"raw_notes": raw_text[:4000]}


async def process_document(pdf_bytes: bytes) -> dict:
    text = await extract_text_from_pdf(pdf_bytes)
    if not text.strip():
        return {"brand_name": "", "raw_notes": "Matn topilmadi."}
    return await summarize_to_structured_passport(text)


async def process_freeform_text(raw_text: str) -> dict:
    if not raw_text.strip():
        return {"brand_name": "", "raw_notes": ""}
    return await summarize_to_structured_passport(raw_text)


def merge_passports(base: dict | None, new: dict | None) -> dict:
    base = dict(base or {})
    new = dict(new or {})
    merged = dict(base)

    for field in _SCALAR_FIELDS:
        new_val = (new.get(field) or "").strip() if isinstance(new.get(field), str) else new.get(field)
        old_val = (base.get(field) or "").strip() if isinstance(base.get(field), str) else base.get(field)
        merged[field] = new_val or old_val or old_val

    for field in _LIST_FIELDS:
        combined = list(base.get(field) or [])
        seen = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in combined}
        for item in new.get(field) or []:
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

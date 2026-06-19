"""Map Supabase/PostgREST errors to user-facing Uzbek messages."""

import ast
import re


def exception_text(exc: Exception) -> str:
    for attr in ("message", "details", "detail"):
        val = getattr(exc, attr, None)
        if val:
            if isinstance(val, dict):
                return str(val.get("message") or val)
            return str(val)

    text = str(exc).strip()
    if text.startswith("{") and "message" in text:
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("message"):
            return str(parsed["message"])
    return text


def parse_missing_column(exc: Exception) -> str | None:
    message = exception_text(exc)
    match = re.search(r"Could not find the '(\w+)' column", message)
    return match.group(1) if match else None


def format_supabase_error(exc: Exception) -> str:
    raw = exception_text(exc)
    missing = parse_missing_column(exc)
    if missing:
        return (
            f"Ma'lumotlar bazasida '{missing}' maydoni yo'q. "
            "Supabase SQL Editor'da supabase/migrations/005_brand_passport_columns.sql "
            "faylini ishga tushiring, so'ng qayta saqlang."
        )
    if "PGRST205" in raw or "Could not find the table" in raw:
        return (
            "Ma'lumotlar bazasi jadvali topilmadi. "
            "Administrator migratsiyalarni ishga tushirishi kerak."
        )
    if "invalid input syntax for type uuid" in raw:
        return "Hisob identifikatori noto'g'ri. Qayta Telegram orqali kiring."
    if len(raw) > 240:
        return "Saqlashda xatolik yuz berdi. Keyinroq qayta urinib ko'ring."
    return raw

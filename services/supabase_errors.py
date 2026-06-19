"""Map Supabase/PostgREST errors to user-facing Uzbek messages."""

import re


def parse_missing_column(message: str) -> str | None:
    match = re.search(r"Could not find the '(\w+)' column", message)
    return match.group(1) if match else None


def format_supabase_error(exc: Exception) -> str:
    raw = str(exc)
    missing = parse_missing_column(raw)
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

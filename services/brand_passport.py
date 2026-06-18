"""
Unified Brand Passport service — single write/read path for all Converza agents.

`brand_passports` is the canonical store. `organizations` holds org metadata
(click_token, business_connection_id) linked by org_id.
"""

import logging
from datetime import datetime, timezone

from db.supabase_client import sb

logger = logging.getLogger(__name__)

# Columns that exist on the live Supabase brand_passports table.
DB_PASSPORT_FIELDS = (
    "brand_name",
    "industry",
    "target_location",
    "target_audience",
    "core_offer",
    "tone",
    "pricing",
    "faq",
    "objections",
    "raw_notes",
)


def normalize_brand_context(
    passport: dict | None,
    legacy: str | dict | None = None,
) -> dict:
    """Build the dict shape expected by closer.py and the web Co-Pilot agents."""
    if isinstance(legacy, str) and legacy.strip():
        return {
            "brand_name": None,
            "industry": None,
            "target_audience": None,
            "core_offer": None,
            "tone": None,
            "brand_voice": None,
            "faq": [],
            "pricing": [],
            "objections": [],
            "raw_notes": legacy,
            "brand_passport": {"raw_notes": legacy},
        }

    if isinstance(legacy, dict) and legacy and not passport:
        passport = legacy

    if not passport:
        return {}

    voice = passport.get("brand_voice") or passport.get("tone") or ""
    return {
        "brand_name": passport.get("brand_name"),
        "industry": passport.get("industry"),
        "target_audience": passport.get("target_audience"),
        "core_offer": passport.get("core_offer"),
        "target_location": passport.get("target_location"),
        "tone": passport.get("tone"),
        "brand_voice": voice,
        "faq": passport.get("faq") or [],
        "pricing": passport.get("pricing") or [],
        "objections": passport.get("objections") or [],
        "hex_colors": passport.get("hex_colors") or [],
        "competitors": passport.get("competitors") or [],
        "avoid_topics": passport.get("avoid_topics") or [],
        "raw_notes": passport.get("raw_notes") or "",
        "brand_passport": passport,
    }


def _maybe_single_row(query) -> dict | None:
    """supabase-py returns None (not an empty APIResponse) when maybe_single finds 0 rows."""
    result = query.maybe_single().execute()
    if result is None:
        return None
    return result.data


def enrich_passport(passport: dict | None) -> dict | None:
    """Add in-memory agent fields that are not stored as DB columns."""
    if not passport:
        return passport
    enriched = dict(passport)
    enriched["brand_voice"] = passport.get("brand_voice") or passport.get("tone") or ""
    enriched.setdefault("hex_colors", [])
    enriched.setdefault("competitors", [])
    enriched.setdefault("avoid_topics", [])
    return enriched


def fetch_passport_by_org(org_id: str) -> dict | None:
    row = _maybe_single_row(
        sb.table("brand_passports").select("*").eq("org_id", org_id)
    )
    return enrich_passport(row)


def fetch_passport_by_id(brand_id: str) -> dict | None:
    row = _maybe_single_row(
        sb.table("brand_passports").select("*").eq("id", brand_id)
    )
    return enrich_passport(row)


def upsert_passport(org_id: str, data: dict) -> dict:
    """Upsert brand_passports by org_id and sync organization metadata."""
    sync_organization(org_id, click_token=data.get("click_token"))

    passport = {k: data[k] for k in DB_PASSPORT_FIELDS if k in data}
    passport["org_id"] = org_id
    passport["updated_at"] = datetime.now(timezone.utc).isoformat()

    existing = fetch_passport_by_org(org_id)
    if existing:
        result = (
            sb.table("brand_passports")
            .update(passport)
            .eq("id", existing["id"])
            .execute()
        )
    else:
        result = sb.table("brand_passports").insert(passport).execute()

    return enrich_passport(result.data[0])


def sync_organization(org_id: str, click_token: str | None = None) -> None:
    """Best-effort write of org metadata.

    `organizations` holds optional metadata (click_token, business_connection_id).
    Login and onboarding must never fail if this write fails (e.g. the table has
    not been provisioned yet), so errors are swallowed and logged.
    """
    row: dict = {"id": org_id}
    if click_token:
        row["click_token"] = click_token
    try:
        sb.table("organizations").upsert(row).execute()
    except Exception as e:
        logger.warning("sync_organization skipped for %s: %s", org_id, e)


def get_org_context(org_id: str) -> dict:
    """Unified read used by DM Closer and onboarding flows."""
    org_row = None
    try:
        org_row = _maybe_single_row(
            sb.table("organizations").select("*").eq("id", org_id)
        )
    except Exception:
        pass

    passport = fetch_passport_by_org(org_id)
    legacy = (org_row or {}).get("brand_context")
    brand_context = normalize_brand_context(passport, legacy)

    connection = {}
    try:
        connection = (
            _maybe_single_row(sb.table("tg_connections").select("*").eq("org_id", org_id))
            or {}
        )
    except Exception:
        pass

    return {
        "id": org_id,
        "brand_context": brand_context,
        "brand_passport_id": (passport or {}).get("id"),
        "click_token": (org_row or {}).get("click_token") or "",
        "business_connection_id": (org_row or {}).get("business_connection_id"),
        "tg_connection": connection,
    }

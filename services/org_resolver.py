"""
Resolve the tenant org_id for inbound Telegram business messages.
"""

import logging
import os

from db.supabase_client import sb
from models.schemas import TelegramUpdate
from services.config import is_production

logger = logging.getLogger(__name__)

DEFAULT_ORG_ID = os.getenv(
    "DEFAULT_ORG_ID", "00000000-0000-0000-0000-000000000001"
)


def owner_org_id(chat_id: int | str) -> str:
    """Map an owner's Telegram chat/user id to the organizations.id text key."""
    return str(chat_id)


def lookup_org_by_connection(connection_id: str) -> str | None:
    """Find organization id linked to a Telegram business_connection_id."""
    if not connection_id:
        return None

    try:
        result = (
            sb.table("organizations")
            .select("id")
            .eq("business_connection_id", connection_id)
            .maybe_single()
            .execute()
        )
        if result and result.data:
            return str(result.data["id"])
    except Exception as exc:
        logger.warning("org lookup failed for connection %s: %s", connection_id, exc)
    return None


def lookup_business_connection_id(org_id: str) -> str | None:
    """Load stored business_connection_id for an organization."""
    if not org_id:
        return None
    try:
        result = (
            sb.table("organizations")
            .select("business_connection_id")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        if result and result.data:
            conn = result.data.get("business_connection_id")
            return str(conn) if conn else None
    except Exception as exc:
        logger.warning("business_connection_id lookup failed for org %s: %s", org_id, exc)
    return None


def resolve_org_id(update: TelegramUpdate) -> str:
    """
    Map an inbound update to the owning organization.

    Priority:
    1. business_message.business_connection_id -> organizations row
    2. DEFAULT_ORG_ID fallback (development only)
    """
    raw = update.model_dump(by_alias=True)
    business_message = raw.get("business_message") or {}
    connection_id = business_message.get("business_connection_id")

    org_id = lookup_org_by_connection(connection_id) if connection_id else None
    if org_id:
        return org_id

    if is_production():
        logger.error(
            "No org for business_connection_id=%s update_id=%s — refusing DEFAULT_ORG_ID",
            connection_id,
            update.update_id,
        )
        raise ValueError(
            f"Unknown business_connection_id: {connection_id or '(missing)'}"
        )

    logger.warning(
        "Falling back to DEFAULT_ORG_ID for connection_id=%s",
        connection_id,
    )
    return DEFAULT_ORG_ID

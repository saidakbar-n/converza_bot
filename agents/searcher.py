"""
Searcher — retrieves context from Supabase to ground the Closer's reply.

Pulls:
- The org's Brand Passport (offer, pricing, FAQ)
- The prospect's conversation history (last N messages)
- The prospect record
"""

from db.supabase_client import sb


async def get_brand_context(org_id: str) -> dict:
    """Return the brand passport for the given org, or an empty dict."""
    result = (
        sb.table("brand_passports")
        .select("*")
        .eq("organization_id", org_id)   # brand_passports still uses organization_id
        .maybe_single()
        .execute()
    )
    return result.data or {}


async def get_conversation_history(
    org_id: str,
    prospect_id: str,
    limit: int = 20,
) -> list[dict]:
    """
    Return the last `limit` messages for this prospect, oldest first,
    formatted as {role, content} pairs for LLM context.
    """
    result = (
        sb.table("messages")
        .select("direction, content, sent_by, created_at")
        .eq("org_id", org_id)
        .eq("prospect_id", prospect_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )

    rows = list(reversed(result.data or []))

    history = []
    for row in rows:
        role = "user" if row["direction"] == "inbound" else "assistant"
        history.append({"role": role, "content": row["content"]})

    return history


async def get_prospect(prospect_id: str) -> dict:
    """Return full prospect record."""
    result = (
        sb.table("prospects")
        .select("*")
        .eq("id", prospect_id)
        .maybe_single()
        .execute()
    )
    return result.data or {}

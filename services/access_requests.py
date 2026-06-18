"""Access approval gate and admin review helpers."""

from datetime import datetime, timezone

from db.supabase_client import sb


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    return username.strip().lstrip("@").lower() or None


def _find_approved(telegram_id: str, telegram_username: str | None) -> dict | None:
    by_id = (
        sb.table("access_requests")
        .select("id")
        .eq("telegram_id", telegram_id)
        .eq("status", "approved")
        .maybe_single()
        .execute()
    )
    if by_id and by_id.data:
        return by_id.data

    username = _normalize_username(telegram_username)
    if not username:
        return None

    result = (
        sb.table("access_requests")
        .select("id")
        .eq("status", "approved")
        .eq("telegram_username", username)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None


def is_user_approved(telegram_id: str | int, telegram_username: str | None = None) -> bool:
    return _find_approved(str(telegram_id), telegram_username) is not None


def list_requests(status: str | None = None, limit: int = 20) -> list[dict]:
    query = sb.table("access_requests").select("*").order("created_at", desc=True).limit(limit)
    if status:
        query = query.eq("status", status)
    result = query.execute()
    return result.data or []


def get_request(request_id: str) -> dict | None:
    result = (
        sb.table("access_requests")
        .select("*")
        .eq("id", request_id)
        .maybe_single()
        .execute()
    )
    if result is None:
        return None
    return result.data


def find_request_by_prefix(prefix: str, status: str | None = None) -> dict:
    """Resolve a request by full or partial UUID prefix."""
    needle = prefix.strip().lower()
    if not needle:
        raise ValueError("So'rov ID si kerak.")

    if len(needle) >= 36:
        row = get_request(needle)
        if row and (not status or row.get("status") == status):
            return row
        raise ValueError("So'rov topilmadi.")

    query = sb.table("access_requests").select("*").order("created_at", desc=True).limit(50)
    if status:
        query = query.eq("status", status)
    rows = query.execute().data or []
    matches = [row for row in rows if str(row["id"]).lower().startswith(needle)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError("Bir nechta so'rov mos keldi. ID ning ko'proq qismini yozing.")
    raise ValueError("So'rov topilmadi.")


def approve_request(request_id: str, review_note: str = "") -> dict:
    result = (
        sb.table("access_requests")
        .update({
            "status": "approved",
            "review_note": review_note.strip() or None,
            "reviewed_at": _now(),
            "updated_at": _now(),
        })
        .eq("id", request_id)
        .eq("status", "pending")
        .execute()
    )
    if not result.data:
        raise ValueError("So'rov topilmadi yoki allaqachon ko'rib chiqilgan.")
    return result.data[0]


def reject_request(request_id: str, review_note: str = "") -> dict:
    result = (
        sb.table("access_requests")
        .update({
            "status": "rejected",
            "review_note": review_note.strip() or None,
            "reviewed_at": _now(),
            "updated_at": _now(),
        })
        .eq("id", request_id)
        .eq("status", "pending")
        .execute()
    )
    if not result.data:
        raise ValueError("So'rov topilmadi yoki allaqachon ko'rib chiqilgan.")
    return result.data[0]


def pending_count() -> int:
    result = (
        sb.table("access_requests")
        .select("id", count="exact")
        .eq("status", "pending")
        .execute()
    )
    return result.count or 0

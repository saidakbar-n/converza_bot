"""Daily business report stats and Uzbek formatting for @ConverzaApp_bot."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from db.supabase_client import sb
from services.brand_passport import fetch_passport_by_org

TZ = ZoneInfo("Asia/Tashkent")

CONDITION_LABELS = {
    "cold": "Sovuq (Cold)",
    "warm": "Iliq (Warm)",
    "purchasing": "Xarid jarayonida (Purchasing)",
    "closed": "Yopilgan (Closed)",
}


def _now_local() -> datetime:
    return datetime.now(TZ)


def _day_start(dt: datetime | None = None) -> datetime:
    base = dt or _now_local()
    return base.replace(hour=0, minute=0, second=0, microsecond=0)


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo("UTC")).isoformat()


def fetch_daily_report_data(org_id: str) -> dict:
    """Collect stats for today's report (Tashkent calendar day)."""
    today_start = _day_start()
    week_start = _day_start() - timedelta(days=today_start.weekday())
    today_iso = _iso_utc(today_start)
    week_iso = _iso_utc(week_start)

    passport = fetch_passport_by_org(org_id) or {}
    brand_name = (passport.get("brand_name") or "").strip() or "Kompaniya"

    msg_rows = (
        sb.table("messages")
        .select("direction, created_at")
        .eq("org_id", org_id)
        .gte("created_at", week_iso)
        .execute()
    ).data or []

    today_inbound = 0
    today_outbound = 0
    week_total = len(msg_rows)
    for row in msg_rows:
        created = row.get("created_at") or ""
        if created >= today_iso:
            if row.get("direction") == "inbound":
                today_inbound += 1
            elif row.get("direction") == "outbound":
                today_outbound += 1

    prospects = (
        sb.table("prospects")
        .select("client_condition")
        .eq("org_id", org_id)
        .execute()
    ).data or []

    conditions = {key: 0 for key in CONDITION_LABELS}
    for prospect in prospects:
        condition = (prospect.get("client_condition") or "cold").lower()
        if condition in conditions:
            conditions[condition] += 1

    return {
        "org_id": org_id,
        "brand_name": brand_name,
        "today_inbound": today_inbound,
        "today_outbound": today_outbound,
        "today_total": today_inbound + today_outbound,
        "week_total": week_total,
        "prospect_conditions": conditions,
        "prospect_total": sum(conditions.values()),
        "report_date": today_start.strftime("%d.%m.%Y"),
    }


def _activity_summary(stats: dict) -> str:
    total = stats["today_total"]
    week = stats["week_total"]
    if total == 0 and week == 0:
        return (
            f"Bugun kompaniya faoliyati doirasida yuborilgan va qabul qilingan xabarlar "
            f"soni 0 ta bo'lib, bu hafta davomida faol aloqalar yo'qligini ko'rsatmoqda."
        )
    if total == 0:
        return (
            f"Bugun yuborilgan va qabul qilingan xabarlar soni 0 ta. "
            f"Bu hafta jami {week} ta xabar qayd etilgan."
        )
    return (
        f"Bugun jami {total} ta xabar almashildi "
        f"({stats['today_inbound']} ta kiruvchi, {stats['today_outbound']} ta chiquvchi). "
        f"Bu hafta jami {week} ta xabar."
    )


def _pipeline_summary(stats: dict) -> str:
    conditions = stats["prospect_conditions"]
    total = stats["prospect_total"]
    lines = ["Mijozlar holatiga kelsak,"]

    if total == 0:
        lines.append(
            "bugun quyidagi holatlarning hech biriga ega mijozlar mavjud emas:"
        )
    else:
        lines.append("joriy pipeline bo'yicha:")

    for key, label in CONDITION_LABELS.items():
        lines.append(f"- {label} - {conditions[key]} ta mijoz")

    if total == 0:
        lines.append("")
        lines.append(
            "Umumiy hisobda, bugun mijozlar bilan aloqalar yo'qligi sababli, "
            "xarid jarayonlari yoki yopilgan mijozlar haqida ma'lumot mavjud emas."
        )
    elif conditions["purchasing"] == 0 and conditions["closed"] == 0:
        lines.append("")
        lines.append(
            "Hozircha xarid jarayonida yoki yopilgan holatdagi mijozlar qayd etilmagan."
        )
    return "\n".join(lines)


def format_daily_report(stats: dict) -> str:
    """Format the canonical Uzbek daily report (deterministic, brand-aware)."""
    header = "📊 KUNLIK HISOBOT"
    intro = (
        f"Bugungi statistik hisobot ({stats['report_date']}):\n\n"
        f"{_activity_summary(stats)}\n\n"
        f"{_pipeline_summary(stats)}"
    )
    brand = stats.get("brand_name")
    if brand and brand != "Kompaniya":
        intro = f"🏢 {brand}\n\n{intro}"
    return f"{header}\n\n{intro}"


async def build_daily_report(org_id: str, *, use_hermes: bool = False) -> str:
    """
    Build daily report. Default: deterministic template from Supabase.
    Optional Hermes pass can polish narrative (falls back on error).
    """
    stats = fetch_daily_report_data(org_id)
    base = format_daily_report(stats)
    if not use_hermes:
        return base

    try:
        from converza_agent.config import hermes_configured
        from converza_agent.runtime import run_agent_text

        if not hermes_configured():
            return base

        import json

        narrative = await run_agent_text(
            "auditor",
            [
                {
                    "role": "user",
                    "content": (
                        "Quyidagi statistikadan foydalanib, qisqa tavsiya qo'shing "
                        "(2-3 gap, O'zbek tilida). Asosiy raqamlarni o'zgartirmang.\n\n"
                        + json.dumps(stats, ensure_ascii=False)
                    ),
                }
            ],
            session_key=f"converza:audit:{org_id}",
            max_tokens=350,
            temperature=0.4,
        )
        tip = (narrative or "").strip()
        if tip:
            return f"{base}\n\n💡 Tavsiya: {tip}"
    except Exception:
        pass
    return base

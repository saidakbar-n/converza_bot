"""Daily report formatting tests."""

from services.daily_report import format_daily_report


def test_empty_report_matches_uzbek_template():
    stats = {
        "brand_name": "Nafis Salon",
        "today_inbound": 0,
        "today_outbound": 0,
        "today_total": 0,
        "week_total": 0,
        "prospect_conditions": {
            "cold": 0,
            "warm": 0,
            "purchasing": 0,
            "closed": 0,
        },
        "prospect_total": 0,
        "report_date": "09.06.2026",
    }
    text = format_daily_report(stats)
    assert "📊 KUNLIK HISOBOT" in text
    assert "Nafis Salon" in text
    assert "0 ta bo'lib" in text
    assert "Sovuq (Cold) - 0 ta mijoz" in text
    assert "faol aloqalar yo'qligini" in text

"""Tests for DM Closer readiness gate."""

from unittest.mock import patch

from services.closer_readiness import assess_closer_readiness, readiness_label


def test_ready_when_passport_and_connection_present():
    org = {"business_connection_id": "conn-1"}
    passport = {
        "brand_name": "Nafis",
        "core_offer": "Salon xizmatlari",
        "pricing": [{"tier": "Pro", "price": "99000"}],
    }
    with (
        patch("services.closer_readiness.is_subscription_active", return_value=True),
        patch("services.closer_readiness.get_org_context", return_value=org),
        patch("services.closer_readiness.fetch_passport_by_org", return_value=passport),
    ):
        ready, reason = assess_closer_readiness("111")
    assert ready is True
    assert reason == ""


def test_not_ready_without_subscription():
    with patch("services.closer_readiness.is_subscription_active", return_value=False):
        ready, reason = assess_closer_readiness("111")
    assert ready is False
    assert reason == "no_active_subscription"


def test_not_ready_without_business_connection():
    with (
        patch("services.closer_readiness.is_subscription_active", return_value=True),
        patch("services.closer_readiness.get_org_context", return_value={}),
        patch("services.closer_readiness.fetch_passport_by_org", return_value=None),
    ):
        ready, reason = assess_closer_readiness("111")
    assert ready is False
    assert reason == "no_business_connection"


def test_not_ready_without_pricing():
    org = {"business_connection_id": "conn-1"}
    passport = {"brand_name": "Nafis", "core_offer": "Offer", "pricing": []}
    with (
        patch("services.closer_readiness.is_subscription_active", return_value=True),
        patch("services.closer_readiness.get_org_context", return_value=org),
        patch("services.closer_readiness.fetch_passport_by_org", return_value=passport),
    ):
        ready, reason = assess_closer_readiness("111")
    assert ready is False
    assert reason == "missing_pricing"


def test_readiness_label_uzbek():
    assert "ulanishi" in readiness_label("no_business_connection")

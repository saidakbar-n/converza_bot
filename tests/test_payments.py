"""Tests for per-org Click payment token resolution."""

from unittest.mock import patch

from services.payments import (
    get_payment_provider_token,
    is_configured_provider_token,
    payments_enabled,
)


VALID_TOKEN = "381764678:TEST:ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def test_is_configured_rejects_placeholders():
    assert is_configured_provider_token("") is False
    assert is_configured_provider_token("test") is False
    assert is_configured_provider_token("123456") is False


def test_is_configured_accepts_botfather_style_token():
    assert is_configured_provider_token(VALID_TOKEN) is True


def test_production_uses_only_org_token():
    org = {"click_token": VALID_TOKEN}
    with patch("services.payments.is_production", return_value=True):
        assert get_payment_provider_token(org) == VALID_TOKEN
        assert payments_enabled(org) is True


def test_production_ignores_env_fallback():
    org = {"click_token": ""}
    with (
        patch("services.payments.is_production", return_value=True),
        patch.dict("os.environ", {"CLICK_TEST_PROVIDER_TOKEN": VALID_TOKEN}),
    ):
        assert get_payment_provider_token(org) == ""


def test_dev_falls_back_to_test_env_token():
    org = {"click_token": ""}
    with (
        patch("services.payments.is_production", return_value=False),
        patch.dict("os.environ", {"CLICK_TEST_PROVIDER_TOKEN": VALID_TOKEN}),
    ):
        assert get_payment_provider_token(org) == VALID_TOKEN

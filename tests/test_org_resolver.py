"""Tests for org_resolver helpers."""

from services.org_resolver import owner_org_id


def test_owner_org_id_from_int():
    assert owner_org_id(123456789) == "123456789"


def test_owner_org_id_from_str():
    assert owner_org_id("987654321") == "987654321"

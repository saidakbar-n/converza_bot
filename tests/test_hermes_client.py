"""Tests for Hermes HTTP client helpers."""

from converza_agent.client import HermesClient, HermesError
from converza_agent.json_utils import extract_json_object


def test_extract_json_from_fence():
    raw = 'Here:\n```json\n{"reply": "Salom"}\n```'
    assert extract_json_object(raw)["reply"] == "Salom"


def test_hermes_client_requires_api_key():
    client = HermesClient(api_key="")
    try:
        client._headers()
        assert False, "expected HermesError"
    except HermesError:
        pass

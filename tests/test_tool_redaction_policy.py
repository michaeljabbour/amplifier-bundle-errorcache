"""Standalone tool redaction matches hook policy on fictional secret fixtures."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "modules/tool-errorcache/amplifier_module_tool_errorcache/redaction.py"
spec = importlib.util.spec_from_file_location("tool_redaction_policy", MODULE)
assert spec is not None and spec.loader is not None
redaction = importlib.util.module_from_spec(spec)
spec.loader.exec_module(redaction)


FIXTURES = [
    "-----BEGIN PRIVATE KEY-----\nFICTIONAL-KEY-MATERIAL\n-----END PRIVATE KEY-----",
    "postgres://fixture:short@database.invalid/db",
    "Authorization: Bearer fixtureTOKEN123456",
    "Basic ZmljdGlvbjpmb28=",
    "password=x api_key='tiny' AUTH_TOKEN=z",
    "sk-fictionalABCDEFGHIJ0123456789",
    "ghp_abcdefghijklmnopqrst0123456789",
    "github_pat_abcdefghijklmnopqrst0123456789",
    "xoxb-fictional-1234567890",
    "AKIAIOSFODNN7EXAMPLE",
    "AIzaFICTIONAL012345678901234567890123456",
    "eyJmaWN0aW9uIjoxfQ.eyJleGFtcGxlIjoyfQ.ZmFrZXNpZ25hdHVyZQ",
    "fixture.person@example.invalid",
    "/Users/fictional/project/file.py /home/fictional/work/file.py",
    "C:\\Users\\fictional\\project\\file.py",
    "MixedFixtureToken0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
]


@pytest.mark.parametrize("fixture", FIXTURES)
def test_string_policy_matches_existing_hook(fixture):
    from amplifier_module_hooks_errorcache import redact_secrets as hook_redact

    assert redaction.redact_secrets(fixture) == hook_redact(fixture)
    assert redaction.redact_secrets(fixture) != fixture


@pytest.mark.parametrize(
    "key", ["password", "passwd", "pwd", "token", "api_key", "apiKey", "API_KEY",
            "access_token", "private-key", "clientSecret", "authorization", "credentials"]
)
def test_sensitive_field_redacts_even_short_or_structured_values(key):
    assert redaction.redact_payload({key: "x"})[key] == "<redacted>"
    assert redaction.redact_payload({key: {"nested": "x"}})[key] == "<redacted>"


def test_nested_evidence_preserves_commands_primitives_and_input():
    payload = {
        "evidence": [{"exit_code": 2, "stderr": "ValueError: password=x", "ok": False}],
        "patch_or_commands": ["python -m pytest -q", "tool --token=short --verbose"],
        "attempts": 3,
        "nothing": None,
        "environment": {"apiKey": "x", "region": "fictional"},
    }
    result = redaction.redact_payload(payload)
    assert result["evidence"] == [{"exit_code": 2, "stderr": "ValueError: password=<redacted>", "ok": False}]
    assert result["patch_or_commands"] == ["python -m pytest -q", "tool --token=<redacted> --verbose"]
    assert result["attempts"] == 3 and result["nothing"] is None
    assert result["environment"] == {"apiKey": "<redacted>", "region": "fictional"}
    assert payload["environment"]["apiKey"] == "x"


def test_dictionary_keys_are_sanitized_without_clobbering_evidence():
    payload = {"first@example.invalid": 1, "second@example.invalid": 2, "token=short": "tiny"}
    result = redaction.redact_payload(payload)
    assert result == {"<redacted:email>": 1, "<redacted:email> [2]": 2, "token=<redacted>": "<redacted>"}


def test_nonsecret_text_and_primitive_keys_remain_unchanged():
    payload = {1: True, "exit_code": 1, "command": "python -m pytest -q", "text": "A" * 45}
    assert redaction.redact_payload(payload) == payload

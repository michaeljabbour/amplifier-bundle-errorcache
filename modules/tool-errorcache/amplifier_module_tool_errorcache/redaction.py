"""Standalone local redaction for ErrorCache tool payloads.

The string policy mirrors hooks-errorcache at commit ebc26d6587a5266f4b207ff7c3c240ce3625d45a.
It is kept self-contained so installing the tool never imports a sibling hook.
Redaction is a bounded heuristic, not proof that arbitrary content contains no secrets.
"""

from __future__ import annotations

import re
from typing import Any

_PEM = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL
)
_URL_CREDS = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_KEY_VALUE = re.compile(
    r"(?i)\b([\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key"
    r"|private[_-]?key|auth|credential)[\w.-]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_KNOWN_TOKENS = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|A(?:KIA|SIA)[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})"
)
_JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_HOME_POSIX = re.compile(r"(?:/Users|/home)/[^\s:'\"]+")
_HOME_WIN = re.compile(r"[A-Za-z]:\\Users\\[^\s:'\"]+", re.IGNORECASE)
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9+/_-]{40,}\b")
_SENSITIVE_FIELD = re.compile(
    r"(?i)(?:^|[_.\-\s])(?:password|passwd|pwd|passphrase|secret|token|api[_-]?key|apikey"
    r"|access[_-]?key|private[_-]?key|auth|authorization|credentials?)(?:$|[_.\-\s=:])"
)


def _redact_long(match: re.Match[str]) -> str:
    token = match.group(0)
    if any(char.isdigit() for char in token) and any(char.isalpha() for char in token):
        return "<redacted:token>"
    return token


def redact_secrets(text: str) -> str:
    """Redact secret-shaped strings locally while retaining surrounding evidence."""
    text = _PEM.sub("<redacted:private-key>", text)
    text = _URL_CREDS.sub(r"\1<redacted>@", text)
    text = _BEARER.sub(r"\1 <redacted>", text)
    text = _KEY_VALUE.sub(r"\1\2<redacted>", text)
    text = _KNOWN_TOKENS.sub("<redacted:token>", text)
    text = _JWT.sub("<redacted:jwt>", text)
    text = _EMAIL.sub("<redacted:email>", text)
    text = _HOME_POSIX.sub("~", text)
    text = _HOME_WIN.sub("~", text)
    return _LONG_TOKEN.sub(_redact_long, text)


def _sensitive_field(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    # Recognize JSON/config casing such as apiKey and clientSecret as well as API_KEY.
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return bool(_SENSITIVE_FIELD.search(normalized))


def redact_payload(value: Any) -> Any:
    """Recursively redact strings/keys and entire values of sensitive fields.

    Nonsensitive primitives are preserved. Redacted key collisions receive a
    deterministic suffix so one evidence field cannot silently overwrite another.
    The input containers are not mutated.
    """
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            safe_key = redact_secrets(key) if isinstance(key, str) else key
            candidate = safe_key
            suffix = 2
            while candidate in result:
                candidate = f"{safe_key} [{suffix}]"
                suffix += 1
            result[candidate] = "<redacted>" if _sensitive_field(key) else redact_payload(item)
        return result
    return value

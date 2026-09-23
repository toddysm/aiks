"""Secret-safe logging helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "<redacted>"
_SENSITIVE_KEY = re.compile(
    r"(?:password|secret|token|authorization|api.?key|access.?key|client.?key.?data|"
    r"connection.?string|account.?key|storage.?key|shared.?access.?key|sas|kubeconfig)",
    re.IGNORECASE,
)
_VALUE_PATTERNS = (
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(AccountKey=)[^;\s]+"),
    re.compile(r"(?i)([?&](?:sig|token|client_secret)=)[^&\s]+"),
    re.compile(
        r"(?i)((?:[\"']?)(?:password|client[_-]?secret|secret|authorization|"
        r"api[_-]?key|access[_-]?key|connection[_-]?string|account[_-]?key|"
        r"storage[_-]?key|shared[_-]?access[_-]?(?:key|signature)|client-key-data|token)"
        r"(?:[\"']?)\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s;,}\]]+)"
    ),
    re.compile(
        r'(?is)("sensitive"\s*:\s*true(?:(?!\n\s*}).){0,500}?"value"\s*:\s*")'
        r"[^\"]*"
    ),
)


def redact_text(value: str) -> str:
    """Redact common credential forms from process output."""

    redacted = value
    for pattern in _VALUE_PATTERNS:
        redacted = pattern.sub(rf"\1{REDACTED}", redacted)
    return redacted


def redact(value: Any) -> Any:
    """Recursively redact sensitive keys and string values."""

    if isinstance(value, Mapping):
        terraform_sensitive = value.get("sensitive") is True
        return {
            str(key): (
                REDACTED
                if _SENSITIVE_KEY.search(str(key))
                or (terraform_sensitive and str(key).casefold() == "value")
                else redact(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(child) for child in value]
    if isinstance(value, str):
        return redact_text(value)
    return value

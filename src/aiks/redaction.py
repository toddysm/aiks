"""Secret-safe logging helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "<redacted>"
_SENSITIVE_KEY = re.compile(
    r"(?:password|secret|token|authorization|connection.?string|account.?key|sas|kubeconfig)",
    re.IGNORECASE,
)
_VALUE_PATTERNS = (
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(AccountKey=)[^;\s]+"),
    re.compile(r"(?i)([?&](?:sig|token|client_secret)=)[^&\s]+"),
    re.compile(
        r"(?i)((?:[\"']?)(?:password|client[_-]?secret|secret|authorization|"
        r"connection[_-]?string|account[_-]?key|sharedaccesssignature|"
        r"client-key-data|token)(?:[\"']?)\s*[:=]\s*)"
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
        return {
            str(key): REDACTED if _SENSITIVE_KEY.search(str(key)) else redact(child)
            for key, child in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(child) for child in value]
    if isinstance(value, str):
        return redact_text(value)
    return value

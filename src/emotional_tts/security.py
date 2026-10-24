"""Secret redaction and logging safety.

Rules:
* API keys are read from the environment and passed only to their vendor.
* Request text, request headers and upstream response bodies are never logged.
* Any diagnostic string can still be scrubbed before it leaves the process.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from pathlib import Path

_SECRET_VALUES: set[str] = set()

_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)([?&](?:api[_-]?key|key|token|access_token|subscription[_-]?key)=)[^&\s]+"),
    re.compile(
        r"(?i)((?:api[_-]?key|authorization|x-api-key|xi-api-key|"
        r"ocp-apim-subscription-key|secret|token)\s*[\"']?\s*[:=]\s*[\"']?)"
        r"[^\"'\s,}]+"
    ),
    re.compile(r"\bsk_[A-Za-z0-9_\-]{8,}\b"),
)


def register_secrets(values: Iterable[str | None]) -> None:
    for value in values:
        if value and len(value) >= 4:
            _SECRET_VALUES.add(value)


def redact(text: object) -> str:
    value = "" if text is None else str(text)
    for secret in _SECRET_VALUES:
        value = value.replace(secret, "***")
    for pattern in _PATTERNS:
        value = pattern.sub(
            lambda match: (match.group(1) + "***") if match.groups() else "***",
            value,
        )
    return value


def sanitize_message(message: object, *, limit: int = 240) -> str:
    """Make an error message safe for API output, logs and diagnostics."""

    value = redact(message)
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = re.sub(r"\s{2,}", " ", value).strip()
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return value


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def install_logging(level: int = logging.INFO) -> None:
    """Install a redacting formatter on existing and future root handlers."""

    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.basicConfig(level=level)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        handler.setFormatter(formatter)
    # Uvicorn configures its own handlers; redact those too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            handler.setFormatter(formatter)
    # HTTP client libraries log every URL at INFO, including provider query
    # parameters (for example MiniMax GroupId). Keep only warnings/errors.
    for name in ("httpx", "httpcore", "aiohttp"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # HTTP client libraries log every URL at INFO, including provider query
    # parameters (for example MiniMax GroupId). Keep only warnings/errors.
    for name in ("httpx", "httpcore", "aiohttp"):
        logging.getLogger(name).setLevel(logging.WARNING)


def audit_env_permissions(path: str | Path) -> list[str]:
    """Return non-fatal warnings about a local .env file."""

    env_path = Path(path)
    warnings: list[str] = []
    if not env_path.exists():
        return warnings
    if os.name == "posix":
        mode = env_path.stat().st_mode & 0o777
        if mode & 0o077:
            warnings.append(
                f"{env_path.name} 对同组或其他用户可读写 (mode={oct(mode)})，"
                "建议执行 chmod 600 .env"
            )
    return warnings

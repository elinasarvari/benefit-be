from __future__ import annotations

import logging
import re
import sys
from typing import Any, TextIO

import structlog
from structlog.typing import EventDict, Processor

from packages.platform.settings import LogFormat, Settings


def bind_context(**ids: Any) -> None:
    """Attach ids (request_id, case_id, workflow_id, ...) to every log line in this context."""
    structlog.contextvars.bind_contextvars(**{k: v for k, v in ids.items() if v is not None})


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


MASKED_KEYS = frozenset(
    {
        "name", "first_name", "last_name", "full_name", "display_name", "username", "address",
        "street", "city", "zip", "dob", "date_of_birth", "birth_date", "ssn", "identity_number",
        "id_number", "document_text", "text", "content", "body", "password", "password_hash",
        "token", "tool_credential", "authorization", "cookie", "set-cookie", "api_key", "secret",
    }
)  # fmt: skip
REQUEST_ID_HEADER = "x-request-id"  # carries the request reference ID API -> workflow -> activity
MASK = "***"
PII_PATTERNS = (
    re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b"),  # identity number (SSN-like)
    re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"),  # JWT-like token
)


def _mask(value: Any, key: str | None = None) -> Any:
    if key is not None and key.lower() in MASKED_KEYS:
        return MASK
    if isinstance(value, dict):
        return {k: _mask(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_mask(v) for v in value]
    if isinstance(value, str):
        for pattern in PII_PATTERNS:
            value = pattern.sub(MASK, value)
    return value


def mask_pii(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Mask personal data by key name and by pattern, at any depth, before rendering (FR-000-11)."""
    masked: EventDict = _mask(dict(event_dict))
    return masked


def _shared_processors(component: str) -> list[Processor]:
    def add_component(_: Any, __: str, event_dict: EventDict) -> EventDict:
        event_dict["component"] = component
        return event_dict

    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        add_component,
        structlog.processors.format_exc_info,
        mask_pii,
    ]


def configure_logging(settings: Settings, component: str, stream: TextIO | None = None) -> None:
    """One structlog pipeline for our code and for stdlib, uvicorn, temporalio and httpx logs."""
    shared = _shared_processors(component)
    renderer: Processor = (
        structlog.dev.ConsoleRenderer()
        if settings.LOG_FORMAT is LogFormat.console
        else structlog.processors.JSONRenderer()
    )
    out = stream if stream is not None else sys.stdout

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.LOG_LEVEL.value]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=out),
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(out)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.LOG_LEVEL.value)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "temporalio", "httpx"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

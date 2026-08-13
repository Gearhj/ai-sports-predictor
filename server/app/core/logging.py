from __future__ import annotations

import logging
import re
import sys

import structlog


_SENSITIVE_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:apiKey|api_key)=)[^&\s\"']+"
)


class _SensitiveQueryFilter(logging.Filter):
    """Redact provider credentials if a request URL reaches a log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _SENSITIVE_QUERY_VALUE.sub(r"\1<redacted>", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_sensitive_query_filter = _SensitiveQueryFilter()


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)
    # httpx logs the complete request URL at INFO. The Odds API authenticates
    # with an apiKey query parameter, so transport logs must never emit at that
    # level. The filter is defense in depth if a warning/error embeds a URL.
    for logger_name in ("httpx", "httpcore"):
        transport_logger = logging.getLogger(logger_name)
        transport_logger.setLevel(logging.WARNING)
        if _sensitive_query_filter not in transport_logger.filters:
            transport_logger.addFilter(_sensitive_query_filter)
    for handler in logging.getLogger().handlers:
        if _sensitive_query_filter not in handler.filters:
            handler.addFilter(_sensitive_query_filter)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


logger = structlog.get_logger("asp")

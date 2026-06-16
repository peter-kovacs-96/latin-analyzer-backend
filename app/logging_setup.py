"""Logging configuration for the Latin Analyzer backend.

Sets up a single console handler with a compact format and installs a filter
that drops the noisy GET /health access-log entries produced by the keepalive
ping every 10 minutes.
"""
import logging
import logging.config


class _NoHealthFilter(logging.Filter):
    """Drop uvicorn access-log records for GET /health pings."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /health" not in record.getMessage()


def setup() -> None:
    """Apply logging configuration. Call once at application startup."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "no_health": {"()": _NoHealthFilter},
            },
            "formatters": {
                "plain": {
                    "format": "%(asctime)s %(levelname)-5s %(message)s",
                    "datefmt": "%H:%M:%S",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "plain",
                    "filters": ["no_health"],
                    "stream": "ext://sys.stdout",
                },
            },
            "loggers": {
                # Route uvicorn logs through our handler so the health filter applies.
                "uvicorn": {
                    "handlers": ["console"],
                    "propagate": False,
                    "level": "INFO",
                },
                "uvicorn.error": {
                    "handlers": ["console"],
                    "propagate": False,
                    "level": "INFO",
                },
                "uvicorn.access": {
                    "handlers": ["console"],
                    "propagate": False,
                    "level": "INFO",
                },
                # App-level logger hierarchy.
                "app": {
                    "handlers": ["console"],
                    "propagate": False,
                    "level": "INFO",
                },
            },
            "root": {
                "handlers": ["console"],
                "level": "WARNING",
            },
        }
    )

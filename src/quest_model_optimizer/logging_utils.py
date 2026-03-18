"""Logging configuration utilities."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> logging.Logger:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    return logging.getLogger("convert-model")

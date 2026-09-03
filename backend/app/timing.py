"""Opt-in, secret-free monotonic timings for runtime latency diagnostics.

Timing is deliberately disabled unless ``DAYPILOT_TIMING_LOGS`` is truthy.  The
logger records only a stable stage name and elapsed milliseconds; callers must
not pass request content, credentials, provider identifiers, or SQL text.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return os.getenv("DAYPILOT_TIMING_LOGS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@contextmanager
def timed(stage: str) -> Iterator[None]:
    """Log the elapsed monotonic duration for a safe, predefined stage name."""
    started = perf_counter()
    try:
        yield
    finally:
        if enabled():
            # Uvicorn's default config keeps application loggers at WARNING;
            # opt-in timings should be visible without changing normal logs.
            if not logger.isEnabledFor(logging.INFO):
                logger.setLevel(logging.INFO)
            logger.info(
                "daypilot_timing stage=%s duration_ms=%.2f",
                stage,
                (perf_counter() - started) * 1000,
            )

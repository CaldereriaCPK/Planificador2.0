"""Utilities to obtain the application's local time in Europe/Madrid."""
from __future__ import annotations

from datetime import datetime
import os
from functools import lru_cache

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - fallback for Python <3.9
    ZoneInfo = None  # type: ignore


_DEFAULT_TZ = os.environ.get("EFIMERO_TZ", "Europe/Madrid")


@lru_cache(None)
def _get_zone() -> ZoneInfo | None:
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(_DEFAULT_TZ)
    except Exception:
        return None


def local_now() -> datetime:
    """Return the current datetime in the configured local timezone."""

    zone = _get_zone()
    if zone is not None:
        return datetime.now(zone)
    return datetime.now()


def local_today():
    """Return today's date in the configured local timezone."""

    return local_now().date()

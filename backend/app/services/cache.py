"""
Tiny in-process TTL cache for read-heavy, slow-changing endpoints.

The leaderboard (/players) and /champions run expensive aggregation queries
over hundreds of thousands of rows, but their results only change when new
data is ingested or scores are recomputed (both manual admin actions). Their
responses are also user-independent — per-user state (watchlist, notes) is
fetched via separate endpoints — so a single shared cache is safe.

Usage:
    from ..services import cache
    key = ("champions", role, patch, min_total_games, sort)
    hit = cache.get(key)
    if hit is not None:
        return hit
    result = ...compute...
    cache.put(key, result)
    return result

Call `cache.clear()` after any pipeline that mutates aggregates/scores so the
next request recomputes immediately instead of serving stale data.
"""
from __future__ import annotations

import threading
import time

# TTL in seconds. Short enough that a background ingest is reflected quickly,
# long enough that continuous navigation is instant.
TTL = 90.0

_store: dict[tuple, tuple[float, object]] = {}
_lock = threading.Lock()


def get(key: tuple):
    """Return the cached value for `key` if still fresh, else None."""
    with _lock:
        hit = _store.get(key)
    if hit is None:
        return None
    ts, value = hit
    if time.monotonic() - ts >= TTL:
        return None
    return value


def put(key: tuple, value) -> None:
    with _lock:
        _store[key] = (time.monotonic(), value)


def clear() -> None:
    """Drop everything — call after ingestion / recompute."""
    with _lock:
        _store.clear()

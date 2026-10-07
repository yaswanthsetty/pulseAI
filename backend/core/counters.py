"""Tiny in-process counter registry shared by every module.

``backend/modules/api/metrics.py`` renders these as Prometheus counters. The
registry lives in ``core`` because business modules (e.g. ``agents``) must not
import ``modules.api`` (import-linter contract 1), yet still need to count
events such as invalid citations or chunks dropped for the context budget.

Counters are per process and lock-guarded, matching the request metrics in
``metrics.py`` (multi-process deployments get one series per process, which
Prometheus aggregates).
"""

import threading

_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}


def incr(name: str, amount: int = 1, **labels: str) -> None:
    """Add ``amount`` to the counter ``name`` for the given label set."""
    if amount <= 0:
        return
    key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
    with _lock:
        _counters[key] = _counters.get(key, 0) + amount


def snapshot() -> dict[str, dict[tuple[tuple[str, str], ...], int]]:
    """Return ``{name: {label_tuple: value}}`` (a copy, safe to iterate)."""
    out: dict[str, dict[tuple[tuple[str, str], ...], int]] = {}
    with _lock:
        for (name, labels), value in _counters.items():
            out.setdefault(name, {})[labels] = value
    return out


def get(name: str, **labels: str) -> int:
    """Current value for one counter series (0 when never incremented)."""
    key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
    with _lock:
        return _counters.get(key, 0)


def reset() -> None:
    """Clear all counters (tests only)."""
    with _lock:
        _counters.clear()

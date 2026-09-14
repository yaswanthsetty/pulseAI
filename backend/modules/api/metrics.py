"""Prometheus metrics exposition (Phase 7 monitoring).

A dependency-free text-exposition endpoint at ``GET /metrics`` (the format
Prometheus scrapes natively). Tracks:

* ``pulseai_http_requests_total``      — request count by method/route/status
* ``pulseai_http_request_seconds``     — latency histogram by route
* ``pulseai_infra_up``                 — 1/0 per infrastructure dependency
* ``pulseai_queue_depth``              — RQ jobs waiting per queue
* ``pulseai_sources_failing``          — sources in FR-3 backoff

Counters live in module-level dicts guarded by a lock (single-process API;
multi-worker deployments get per-process series which Prometheus aggregates).
"""

import threading
import time

from fastapi import APIRouter, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

router = APIRouter(tags=["ops"])

_lock = threading.Lock()
_request_counts: dict[tuple[str, str, int], int] = {}
_request_latency_buckets: dict[tuple[str, str], list[float]] = {}
_request_latency_sum: dict[tuple[str, str], float] = {}
_buckets = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, float("inf"))


def _normalise_route(path: str) -> str:
    """Collapse UUID/path segments so cardinals stay bounded."""
    if not path:
        return "other"
    parts = path.strip("/").split("/")
    collapsed = []
    for part in parts[:4]:  # depth cap: /api/v1/<module>/<action>
        collapsed.append("param" if len(part) > 24 or part.isdigit() else part)
    return "/".join(collapsed) or "root"


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record request count + latency for every HTTP request."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            elapsed = time.perf_counter() - start
            route = _normalise_route(request.url.path)
            key_count = (request.method, route, status)
            key_latency = (request.method, route)
            with _lock:
                _request_counts[key_count] = _request_counts.get(key_count, 0) + 1
                _request_latency_sum[key_latency] = (
                    _request_latency_sum.get(key_latency, 0.0) + elapsed
                )
                buckets = _request_latency_buckets.setdefault(key_latency, [0.0] * len(_buckets))
                for i, edge in enumerate(_buckets):
                    if elapsed <= edge:
                        buckets[i] += 1
                        break
        return response


@router.get("/metrics")
def metrics() -> Response:
    """Prometheus text exposition of request/infra/queue metrics."""
    lines: list[str] = []

    lines.append("# HELP pulseai_http_requests_total HTTP request count.")
    lines.append("# TYPE pulseai_http_requests_total counter")
    with _lock:
        counts = dict(_request_counts)
        latencies = {
            k: (list(v), _request_latency_sum.get(k, 0.0))
            for k, v in _request_latency_buckets.items()
        }
    for (method, route, status), count in sorted(counts.items()):
        lines.append(
            f'pulseai_http_requests_total{{method="{method}",'
            f'route="{route}",status="{status}"}} {count}'
        )

    lines.append("# HELP pulseai_http_request_seconds HTTP request latency.")
    lines.append("# TYPE pulseai_http_request_seconds histogram")
    for (method, route), (bucket_counts, total) in sorted(latencies.items()):
        for edge, count in zip(_buckets, bucket_counts, strict=True):
            le = "+Inf" if edge == float("inf") else str(edge)
            lines.append(
                f'pulseai_http_request_seconds_bucket{{method="{method}",'
                f'route="{route}",le="{le}"}} {int(count)}'
            )
        lines.append(
            f'pulseai_http_request_seconds_sum{{method="{method}",route="{route}"}} {total:.6f}'
        )
        lines.append(
            f'pulseai_http_request_seconds_count{{method="{method}",'
            f'route="{route}"}} {int(bucket_counts[-1])}'
        )

    lines.append("# HELP pulseai_infra_up Infrastructure dependency reachability.")
    lines.append("# TYPE pulseai_infra_up gauge")
    for name, ok in _probe_infra():
        lines.append(f'pulseai_infra_up{{dependency="{name}"}} {1 if ok else 0}')

    lines.append("# HELP pulseai_queue_depth RQ jobs waiting per queue.")
    lines.append("# TYPE pulseai_queue_depth gauge")
    for queue, depth in _probe_queues():
        lines.append(f'pulseai_queue_depth{{queue="{queue}"}} {depth}')

    lines.append("# HELP pulseai_sources_failing Sources in FR-3 backoff.")
    lines.append("# TYPE pulseai_sources_failing gauge")
    lines.append(f"pulseai_sources_failing {_probe_failing_sources()}")

    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


def _probe_infra() -> list[tuple[str, bool]]:
    results: list[tuple[str, bool]] = []
    # Postgres
    try:
        from sqlalchemy import text

        from backend.core.database import SessionLocal

        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            results.append(("postgres", True))
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - metrics must never raise
        results.append(("postgres", False))
    # Redis
    try:
        from backend.core.queue import get_redis

        get_redis().ping()
        results.append(("redis", True))
    except Exception:  # noqa: BLE001
        results.append(("redis", False))
    # Qdrant
    try:
        import httpx

        from backend.core.config import settings

        response = httpx.get(f"{settings.qdrant_url}/healthz", timeout=2.0)
        results.append(("qdrant", response.status_code == 200))
    except Exception:  # noqa: BLE001
        results.append(("qdrant", False))
    return results


def _probe_queues() -> list[tuple[str, int]]:
    try:
        from backend.core.queue import CLUSTER_QUEUE, EMBED_QUEUE, INGEST_QUEUE, get_redis

        r = get_redis()
        depths = []
        for name in (INGEST_QUEUE, EMBED_QUEUE, CLUSTER_QUEUE):
            depth = r.llen(f"rq:queue:{name}")
            depths.append((name, int(depth)))
        return depths
    except Exception:  # noqa: BLE001
        return []


def _probe_failing_sources() -> int:
    try:
        from sqlalchemy import select

        from backend.core.database import SessionLocal
        from backend.db.models import Source

        db = SessionLocal()
        try:
            return int(
                db.execute(select(Source.id).where(Source.consecutive_failures > 0))
                .scalars()
                .all()
                .__len__()
            )
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return 0

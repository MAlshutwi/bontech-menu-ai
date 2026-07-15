"""
app/runtime.py - operational API helpers: kill switch, metrics, rate limit, and API key.
All state is in memory and suitable for an internal service. No credentials are printed.
"""
from __future__ import annotations
import os
import time
import uuid
from collections import deque, defaultdict
from pathlib import Path

from .config import ROOT, SERVING


# ---------------- Kill switch ----------------
def kill_switch_status():
    """Return kill switch status for each request without requiring restart."""
    if os.environ.get("AI_RECO_DISABLED"):
        return True, "env AI_RECO_DISABLED set"
    f = SERVING.get("kill_switch_file")
    if f and (ROOT / f).exists():
        return True, f"kill-switch file present ({f})"
    if not SERVING.get("enabled", True):
        return True, "serving.enabled=false in config"
    return False, ""


# ---------------- API key ----------------
def api_key_required() -> bool:
    return bool(SERVING.get("require_api_key", False))


def check_api_key(provided: str | None) -> bool:
    if not api_key_required():
        return True
    expected = os.environ.get("API_KEY", "")
    return bool(expected) and provided == expected


# ---------------- Tenant isolation ----------------
def tenant_allowed(api_key: str | None, restaurant_id: int) -> bool:
    mapping = SERVING.get("tenant_allowed_restaurants") or {}
    if not mapping:
        return True  # Tenant mapping disabled.
    allowed = mapping.get(api_key or "", None)
    if allowed is None:
        return False
    return int(restaurant_id) in set(int(x) for x in allowed)


# ---------------- Rate limiter (fixed window/minute) ----------------
class RateLimiter:
    def __init__(self, per_min: int):
        self.per_min = int(per_min)
        self._w = {}  # key -> [minute, count]

    def allow(self, key: str) -> bool:
        if self.per_min <= 0:
            return True
        minute = int(time.time() // 60)
        w = self._w.get(key)
        if not w or w[0] != minute:
            self._w[key] = [minute, 1]
            return True
        if w[1] >= self.per_min:
            return False
        w[1] += 1
        return True


_rl = RateLimiter(SERVING.get("rate_limit_per_min", 0))


def rate_limit_ok(key: str) -> bool:
    return _rl.allow(key)


# ---------------- Metrics (in-memory; resets on restart) ----------------
class Metrics:
    def __init__(self):
        self.total = 0
        self.errors = 0
        self.rejected = 0          # Empty or rejected recommendations.
        self.disabled_hits = 0     # Requests blocked by the kill switch.
        self.by_endpoint = defaultdict(int)
        self.lat = deque(maxlen=3000)

    def record(self, endpoint, latency_ms, status, rejected=False):
        self.total += 1
        self.by_endpoint[endpoint] += 1
        self.lat.append(float(latency_ms))
        if status >= 500:
            self.errors += 1
        if rejected:
            self.rejected += 1

    def snapshot(self):
        lat = sorted(self.lat)
        def pct(p):
            if not lat:
                return None
            i = min(len(lat) - 1, int(round(p / 100 * (len(lat) - 1))))
            return round(lat[i], 2)
        return {
            "total_requests": self.total,
            "errors": self.errors,
            "rejected_recommendations": self.rejected,
            "kill_switch_hits": self.disabled_hits,
            "success_rate": round(1 - self.errors / self.total, 4) if self.total else None,
            "latency_ms_p50": pct(50),
            "latency_ms_p95": pct(95),
            "by_endpoint": dict(self.by_endpoint),
            "note": "in-memory; resets on restart",
        }


METRICS = Metrics()


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]

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
from threading import Lock

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
    """Thread-safe, bounded fixed-window limiter.

    Only the current minute is useful for a fixed-window decision, so expired
    identities are pruned whenever the minute changes.  ``max_keys`` prevents a
    stream of made-up identities from growing process memory without bound.
    """

    def __init__(self, per_min: int, max_keys: int = 4096):
        self.per_min = int(per_min)
        self.max_keys = max(1, int(max_keys))
        self._w = {}  # key -> [minute, count]
        self._minute = None
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        if self.per_min <= 0:
            return True
        minute = int(time.time() // 60)
        # Bound attacker-controlled input even if a future caller passes a raw
        # header instead of the normalized IP/API identity used by main.py.
        normalized_key = str(key or "anon")[:160]
        with self._lock:
            if self._minute != minute:
                self._w.clear()
                self._minute = minute
            w = self._w.get(normalized_key)
            if w is None:
                if len(self._w) >= self.max_keys:
                    return False
                self._w[normalized_key] = [minute, 1]
                return True
            if w[1] >= self.per_min:
                return False
            w[1] += 1
            return True

    def reset(self):
        """Clear in-memory state; intended for tests and operational resets."""
        with self._lock:
            self._w.clear()
            self._minute = None


_base_limit = max(0, int(SERVING.get("rate_limit_per_min", 0)))
_limiters = {
    "default": RateLimiter(_base_limit),
    # Public menu reads are intentionally available without embedding a secret
    # in the browser, but are still bounded by client address.
    "public_read": RateLimiter(_base_limit or 120),
    # Full cache-bypassing menu reads are intentionally conservative.
    "fresh_read": RateLimiter(max(3, min(12, (_base_limit or 120) // 10))),
    # A single-item stock check is lightweight and runs before every cart
    # increase, so it needs its own budget instead of sharing the refresh cap.
    "availability": RateLimiter(max(30, min(120, _base_limit or 120))),
    "events": RateLimiter(max(10, min(120, _base_limit or 120))),
}


def rate_limit_ok(key: str, scope: str = "default") -> bool:
    limiter = _limiters.get(scope, _limiters["default"])
    return limiter.allow(key)


def reset_rate_limits():
    for limiter in _limiters.values():
        limiter.reset()


# ---------------- Metrics (in-memory; resets on restart) ----------------
class Metrics:
    def __init__(self, max_endpoints: int = 128):
        self.total = 0
        self.errors = 0
        self.rejected = 0          # Empty or rejected recommendations.
        self.disabled_hits = 0     # Requests blocked by the kill switch.
        self.by_endpoint = defaultdict(int)
        self.lat = deque(maxlen=3000)
        self.max_endpoints = max(1, int(max_endpoints))
        self._lock = Lock()

    def record(self, endpoint, latency_ms, status, rejected=False):
        endpoint = str(endpoint or "unmatched")[:160]
        with self._lock:
            self.total += 1
            if endpoint not in self.by_endpoint and len(self.by_endpoint) >= self.max_endpoints:
                endpoint = "other"
            self.by_endpoint[endpoint] += 1
            self.lat.append(float(latency_ms))
            if status >= 500:
                self.errors += 1
            if rejected:
                self.rejected += 1

    def record_rejected(self):
        with self._lock:
            self.rejected += 1

    def record_disabled(self):
        with self._lock:
            self.disabled_hits += 1

    def snapshot(self):
        with self._lock:
            total = self.total
            errors = self.errors
            rejected = self.rejected
            disabled_hits = self.disabled_hits
            by_endpoint = dict(self.by_endpoint)
            lat = sorted(self.lat)
        def pct(p):
            if not lat:
                return None
            i = min(len(lat) - 1, int(round(p / 100 * (len(lat) - 1))))
            return round(lat[i], 2)
        return {
            "total_requests": total,
            "errors": errors,
            "rejected_recommendations": rejected,
            "kill_switch_hits": disabled_hits,
            "success_rate": round(1 - errors / total, 4) if total else None,
            "latency_ms_p50": pct(50),
            "latency_ms_p95": pct(95),
            "by_endpoint": by_endpoint,
            "note": "in-memory; resets on restart",
        }


METRICS = Metrics()


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]

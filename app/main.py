"""
app/main.py - internal POS recommendations API (FastAPI).

Loads the final serialized model file at startup and serves recommendations from memory.
Operational controls: kill switch, optional API key, rate limit, tenant scope,
request_id per request, live metrics, and JSONL event logging.

Run:
    uvicorn app.main:app --host 127.0.0.1 --port 8000
Security: internal service only; bind to loopback or a private network.
"""
from __future__ import annotations
import json
import hmac
import logging
import math
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock

from fastapi import FastAPI, HTTPException, Query, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from .config import MODEL_VERSION, SERVING
from .model_loader import final_model_path, load_model
from .recommender import get_engine
from .db import (fetch_restaurants_with_menu_counts,
                 fetch_restaurant_menu_with_sizes,
                 fetch_restaurant_item_availability, fetch_restaurant_trend_counts,
                 check_database_connection)
from .runtime import (kill_switch_status, check_api_key, api_key_required, tenant_allowed,
                      rate_limit_ok, new_request_id, METRICS)
from .demo_page import DEMO_HTML
from .clean_menu_page import CLEAN_MENU_APP_HTML
from .schemas import (RecommendationRequest, RecommendationResponse, HealthResponse,
                      RecommendationEvent, EventAck, WidgetRecommendationRequest,
                      WidgetRecommendationResponse)
from .trend import build_current_trend_nowcast, TREND_MODEL_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("rec-api")

MAX_CART = int(SERVING.get("max_cart_items", 50))
_META = {}
ROOT_DIR = Path(__file__).resolve().parent.parent
EVENTS_DIR = ROOT_DIR / "reports" / "events"
EVENTS_DIR.mkdir(parents=True, exist_ok=True)
WIDGET_DIR = ROOT_DIR / "delivery" / "final_model" / "widget"
FINAL_DELIVERY_DEMO = ROOT_DIR / "delivery" / "final_model" / "demo" / "index.html"
LEGACY_FINAL_DELIVERY_DEMO = ROOT_DIR / "التسليم" / "ديمو" / "index.html"
LOVABLE_MENU_DIST = ROOT_DIR / "ToCoun" / "LovableMenuAI" / "dist"
LOVABLE_MENU_INDEX = LOVABLE_MENU_DIST / "index.html"
RESTAURANTS_CACHE_TTL_SECONDS = 5 * 60
MENU_CACHE_TTL_SECONDS = 15
MENU_CACHE_MAX_ENTRIES = 256
TREND_CACHE_TTL_SECONDS = 5 * 60
TREND_CACHE_MAX_ENTRIES = 256
DB_READINESS_CACHE_TTL_SECONDS = 10
MAX_REQUEST_BODY_BYTES = 64 * 1024
EVENTS_MAX_BYTES_PER_DAY = 25 * 1024 * 1024
_LIVE_CACHE_LOCK = RLock()
_RESTAURANTS_LOAD_LOCK = Lock()
_RESTAURANTS_CACHE = None
_MENU_CACHE = {}
_TREND_CACHE = {}
_MENU_LOAD_LOCKS = tuple(Lock() for _ in range(64))
_TREND_LOAD_LOCKS = tuple(Lock() for _ in range(64))
_DB_READINESS_LOCK = Lock()
_DB_READINESS_CACHE = None
_EVENTS_WRITE_LOCK = Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        model = load_model()
        _META.update(model.metadata)
        if api_key_required():
            import os
            if not os.environ.get("API_KEY"):
                log.warning("require_api_key=true but API_KEY env not set — all requests will be rejected")
        else:
            log.warning(
                "API key auth disabled; public browser endpoints remain protected by IP rate limits"
            )
        log.info("model file loaded | path=%s | model_version=%s", final_model_path(), model.model_version)
    except Exception:
        log.exception("failed to load model file at startup")
        raise
    yield


app = FastAPI(title="POS Recommendations API", version=MODEL_VERSION, lifespan=lifespan)
if (LOVABLE_MENU_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=LOVABLE_MENU_DIST / "assets"), name="lovable-menu-assets")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "null",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8011",
        "http://localhost:8011",
    ],
    allow_origin_regex=r"https://([a-z0-9-]+\.)*lovable\.app",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "X-Request-Id"],
    expose_headers=["X-Request-Id"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)


@app.middleware("http")
async def observability(request: Request, call_next):
    rid = request.headers.get("X-Request-Id") or new_request_id()
    request.state.request_id = rid
    t0 = time.perf_counter()
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            body_too_large = int(content_length) > MAX_REQUEST_BODY_BYTES
        except ValueError:
            body_too_large = True
        if body_too_large:
            latency = 1000 * (time.perf_counter() - t0)
            METRICS.record(f"{request.method} request_body_rejected", latency, 413)
            resp = JSONResponse(
                status_code=413,
                content={"detail": "request body too large", "request_id": rid},
            )
            resp.headers["X-Request-Id"] = rid
            return resp
    try:
        resp = await call_next(request)
    except Exception:
        latency = 1000 * (time.perf_counter() - t0)
        METRICS.record(_metric_endpoint(request), latency, 500)
        log.exception("request_id=%s %s %s -> 500 (%.1fms)", rid, request.method, request.url.path, latency)
        return JSONResponse(status_code=500, content={"detail": "internal error", "request_id": rid})
    latency = 1000 * (time.perf_counter() - t0)
    METRICS.record(_metric_endpoint(request), latency, resp.status_code)
    resp.headers["X-Request-Id"] = rid
    # Structured request log without PII or credentials.
    log.info("request_id=%s %s %s -> %s (%.1fms)", rid, request.method, request.url.path, resp.status_code, latency)
    return resp


def _metric_endpoint(request: Request) -> str:
    """Use a route template, never attacker-controlled path values, as a key."""
    route = request.scope.get("route")
    template = getattr(route, "path", None) or "unmatched"
    return f"{request.method} {template}"


def _client_rate_key(request: Request) -> str:
    """Stable identity that cannot be rotated with a made-up X-API-Key."""
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def _is_admin_key(x_api_key: str | None) -> bool:
    expected = os.environ.get("API_KEY", "")
    return bool(expected and x_api_key and hmac.compare_digest(x_api_key, expected))


def _guard(request: Request, x_api_key, restaurant_id=None):
    """Shared guard for kill switch, API key, rate limit, and tenant scope."""
    disabled, reason = kill_switch_status()
    if disabled:
        METRICS.record_disabled()
        raise HTTPException(status_code=503, detail=f"AI recommendations disabled: {reason}")
    if not check_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    key = _client_rate_key(request)
    if not rate_limit_ok(key):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    if restaurant_id is not None and not tenant_allowed(x_api_key, restaurant_id):
        raise HTTPException(status_code=403, detail="restaurant not allowed for this API key")


def _guard_public_read(
    request: Request, *, fresh: bool = False, rate_scope: str | None = None
):
    """Rate-limit browser-safe active menu reads without requiring a secret."""
    if fresh and _is_admin_key(request.headers.get("X-API-Key")):
        scope = "default"
    elif rate_scope:
        scope = rate_scope
    else:
        scope = "fresh_read" if fresh else "public_read"
    if not rate_limit_ok(_client_rate_key(request), scope=scope):
        raise HTTPException(status_code=429, detail="rate limit exceeded")


def _unique_positive_ints(values):
    out = []
    seen = set()
    for value in values or []:
        ivalue = int(value)
        if ivalue < 1:
            continue
        if ivalue not in seen:
            seen.add(ivalue)
            out.append(ivalue)
    return out


def _known_restaurant(eng, restaurant_id: int) -> bool:
    rid = int(restaurant_id)
    return bool(
        eng.rest_items.get(rid)
        or rid in eng.restaurants_with_pop
        or rid in eng.restaurants_with_fbt
    )


def _widget_item(eng, restaurant_id: int, item: dict, recommendation_type: str, cart_set: set[int]):
    item_id = int(item.get("item_id"))
    available = eng.rest_items.get(int(restaurant_id), set())
    disabled_reason = None
    addable = True
    if item_id in cart_set:
        addable = False
        disabled_reason = "already_in_cart"
    elif available and item_id not in available:
        addable = False
        disabled_reason = "not_available_in_restaurant_menu"
    return {
        "item_id": item_id,
        "title_ar": item.get("title_ar") or "",
        "title_en": item.get("title_en") or "",
        "score": float(item.get("score") or 0.0),
        "source": item.get("source") or "unknown",
        "recommendation_type": recommendation_type,
        "reason": item.get("reason") or "",
        "evidence": item.get("evidence") or {},
        "addable": addable,
        "disabled_reason": disabled_reason,
    }


def _pick_group_items(eng, restaurant_id: int, payload: dict, cart_set: set[int], seen: set[int],
                      preferred_types, limit: int):
    groups = {g.get("type"): g for g in payload.get("recommendation_groups", [])}
    out = []
    skipped = []
    for recommendation_type in preferred_types:
        group = groups.get(recommendation_type)
        if not group:
            continue
        for raw_item in group.get("items", []):
            item_id = int(raw_item.get("item_id"))
            if item_id in seen:
                skipped.append(f"duplicate_item:{item_id}")
                continue
            formatted = _widget_item(eng, restaurant_id, raw_item, recommendation_type, cart_set)
            if not formatted["addable"]:
                skipped.append(f"{formatted['disabled_reason']}:{item_id}")
                continue
            out.append(formatted)
            seen.add(item_id)
            if len(out) >= limit:
                return out, skipped
    return out, skipped


def _optional_number(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _availability_state(mode, current_value):
    normalized = str(mode or "").strip().lower()
    remaining = _optional_number(current_value)
    configured = bool(normalized)
    # No availability rule means the catalog's active/published state remains
    # authoritative. Once a stock rule exists, however, unknown values fail closed.
    is_available = not configured
    reason = "availability_unconfigured"

    if normalized == "outofstock":
        is_available = False
        reason = "out_of_stock"
    elif normalized == "staticquantity":
        if remaining is None:
            reason = "quantity_unknown"
        elif remaining <= 0:
            is_available = False
            reason = "quantity_depleted"
        else:
            is_available = True
            reason = "quantity_available"
    elif normalized == "dependonstock":
        if remaining is None:
            reason = "stock_managed_unknown"
        elif remaining <= 0:
            is_available = False
            reason = "stock_depleted"
        else:
            is_available = True
            reason = "stock_available"
    elif normalized == "alwaysavailable":
        is_available = True
        reason = "always_available"
    elif configured:
        reason = "availability_mode_unknown"

    return {
        "availability_mode": mode or None,
        "availability_configured": configured,
        "remaining_quantity": remaining,
        "is_available": is_available,
        "availability_reason": reason,
    }


def _reset_live_data_caches():
    """Clear request-time caches (used by tests and operational refreshes)."""
    global _RESTAURANTS_CACHE, _DB_READINESS_CACHE
    with _LIVE_CACHE_LOCK:
        _RESTAURANTS_CACHE = None
        _MENU_CACHE.clear()
        _TREND_CACHE.clear()
        _DB_READINESS_CACHE = None


def _cached_restaurants_payload(fresh: bool = False):
    global _RESTAURANTS_CACHE
    now = time.monotonic()
    if not fresh:
        with _LIVE_CACHE_LOCK:
            cached = _RESTAURANTS_CACHE
            if cached is not None and cached[0] > now:
                return cached[1]

    with _RESTAURANTS_LOAD_LOCK:
        now = time.monotonic()
        if not fresh:
            with _LIVE_CACHE_LOCK:
                cached = _RESTAURANTS_CACHE
                if cached is not None and cached[0] > now:
                    return cached[1]
        rows = json.loads(
            fetch_restaurants_with_menu_counts().to_json(orient="records", force_ascii=False)
        )
        payload = {"restaurants": rows, "count": len(rows), "source": "live_database"}
        with _LIVE_CACHE_LOCK:
            _RESTAURANTS_CACHE = (
                time.monotonic() + RESTAURANTS_CACHE_TTL_SECONDS,
                payload,
            )
        return payload


def _fetch_live_menu_payload(restaurant_id: int, include_inactive: bool = False):
    frame = fetch_restaurant_menu_with_sizes(restaurant_id, include_inactive)
    if frame.empty:
        raise HTTPException(status_code=404, detail="restaurant not found")
    raw = json.loads(frame.to_json(orient="records", force_ascii=False))
    first_row = raw[0]
    restaurant = {
        "restaurant_id": int(first_row["restaurant_id"]),
        "name": first_row.get("restaurant_name") or f"rest_{int(restaurant_id)}",
        "name_ar": first_row.get("restaurant_name_ar") or "",
    }

    items_by_id = {}
    for row in raw:
        if row.get("item_id") is None:
            continue
        item_id = int(row["item_id"])
        row_availability = _availability_state(
            row.get("availability_mode"),
            row.get("current_availability_value"),
        )
        item = items_by_id.setdefault(item_id, {
            "item_id": item_id,
            "restaurant_id": int(row["restaurant_id"]),
            "title_ar": row.get("title_ar") or "",
            "title_en": row.get("title_en") or "",
            "category_id": row.get("category_id"),
            "category_ar": row.get("category_ar") or "",
            "category_en": row.get("category_en") or "",
            "is_published": bool(row.get("is_published")),
            "is_deleted": bool(row.get("is_deleted")),
            "is_combo": bool(row.get("is_combo")),
            "calories": row.get("calories"),
            "sizes": [],
            "_row_availability": row_availability,
        })
        if row.get("item_size_id") is not None:
            item["sizes"].append({
                "item_size_id": int(row["item_size_id"]),
                "title_ar": row.get("size_ar") or "",
                "title_en": row.get("size_en") or "",
                "code": row.get("size_code") or "",
                "price": row.get("price"),
                "takeaway_price": row.get("takeaway_price"),
                "is_deleted": bool(row.get("size_is_deleted")),
                **row_availability,
            })

    items = list(items_by_id.values())
    for item in items:
        row_availability = item.pop("_row_availability")
        if item["sizes"]:
            available_size_count = sum(1 for size in item["sizes"] if size["is_available"])
            item["available_size_count"] = available_size_count
            item["is_available"] = available_size_count > 0
            item["availability_reason"] = (
                "available_size_exists" if available_size_count else "all_sizes_unavailable"
            )
        else:
            item["available_size_count"] = 0
            item["is_available"] = row_availability["is_available"]
            item["availability_reason"] = row_availability["availability_reason"]

    categories = {}
    for item in items:
        key = str(item["category_id"] or "uncategorized")
        category = categories.setdefault(key, {
            "category_id": item["category_id"],
            "title_ar": item["category_ar"],
            "title_en": item["category_en"],
            "count": 0,
        })
        category["count"] += 1

    return {
        "restaurant": restaurant,
        "items": items,
        "categories": list(categories.values()),
        "count": len(items),
        "source": "live_database",
        "include_inactive": include_inactive,
    }


def _live_menu_payload(restaurant_id: int, include_inactive: bool = False, fresh: bool = False):
    key = (int(restaurant_id), bool(include_inactive))
    load_lock = _MENU_LOAD_LOCKS[hash(key) % len(_MENU_LOAD_LOCKS)]
    now = time.monotonic()
    if not fresh:
        with _LIVE_CACHE_LOCK:
            cached = _MENU_CACHE.get(key)
            if cached is not None and cached[0] > now:
                return cached[1]

    with load_lock:
        now = time.monotonic()
        if not fresh:
            with _LIVE_CACHE_LOCK:
                cached = _MENU_CACHE.get(key)
                if cached is not None and cached[0] > now:
                    return cached[1]
        payload = _fetch_live_menu_payload(int(restaurant_id), bool(include_inactive))
        with _LIVE_CACHE_LOCK:
            expired_keys = [cache_key for cache_key, value in _MENU_CACHE.items() if value[0] <= time.monotonic()]
            for expired_key in expired_keys:
                _MENU_CACHE.pop(expired_key, None)
            if key not in _MENU_CACHE and len(_MENU_CACHE) >= MENU_CACHE_MAX_ENTRIES:
                oldest_key = min(_MENU_CACHE, key=lambda cache_key: _MENU_CACHE[cache_key][0])
                _MENU_CACHE.pop(oldest_key, None)
            _MENU_CACHE[key] = (time.monotonic() + MENU_CACHE_TTL_SECONDS, payload)
        return payload


def _invalidate_menu_cache(restaurant_id: int):
    rid = int(restaurant_id)
    with _LIVE_CACHE_LOCK:
        for key in [key for key in _MENU_CACHE if key[0] == rid]:
            _MENU_CACHE.pop(key, None)


def _cached_trend_counts(restaurant_id: int, fresh: bool = False):
    rid = int(restaurant_id)
    load_lock = _TREND_LOAD_LOCKS[hash(rid) % len(_TREND_LOAD_LOCKS)]
    now = time.monotonic()
    if not fresh:
        with _LIVE_CACHE_LOCK:
            cached = _TREND_CACHE.get(rid)
            if cached is not None and cached[0] > now:
                return cached[1]

    with load_lock:
        now = time.monotonic()
        if not fresh:
            with _LIVE_CACHE_LOCK:
                cached = _TREND_CACHE.get(rid)
                if cached is not None and cached[0] > now:
                    return cached[1]
        frame = fetch_restaurant_trend_counts(rid)
        with _LIVE_CACHE_LOCK:
            expired = [key for key, value in _TREND_CACHE.items() if value[0] <= time.monotonic()]
            for key in expired:
                _TREND_CACHE.pop(key, None)
            if rid not in _TREND_CACHE and len(_TREND_CACHE) >= TREND_CACHE_MAX_ENTRIES:
                oldest = min(_TREND_CACHE, key=lambda key: _TREND_CACHE[key][0])
                _TREND_CACHE.pop(oldest, None)
            _TREND_CACHE[rid] = (time.monotonic() + TREND_CACHE_TTL_SECONDS, frame)
        return frame


def _current_trend_payload(restaurant_id: int, period_key: str | None, period_ar: str | None, model):
    try:
        max_freshness_hours = max(0.0, float(os.environ.get("TREND_MAX_FRESHNESS_HOURS", "48")))
    except (TypeError, ValueError):
        max_freshness_hours = 48.0
    try:
        counts = _cached_trend_counts(int(restaurant_id))
        return build_current_trend_nowcast(
            counts,
            period_key=period_key,
            period_ar=period_ar,
            titles=model.item_titles,
            item_categories=model.item_categories,
            item_groups=model.item_groups,
            limit=50,
            max_freshness_hours=max_freshness_hours,
        )
    except Exception:
        log.warning("current trend live query failed for restaurant_id=%s", restaurant_id, exc_info=True)
        return {
            "model_key": TREND_MODEL_KEY,
            "status": "unavailable",
            "why_ar": "تعذر قراءة الطلبات الحديثة الآن؛ لم يتم عرض ترند قديم على أنه حالي.",
            "unavailable_reason": "live_trend_data_source_unavailable",
            "items": [],
            "data_as_of": None,
            "latest_order_at": None,
            "freshness_status": "no_data",
            "scope": None,
            "is_forecast": False,
        }


def _database_readiness(fresh: bool = False):
    global _DB_READINESS_CACHE
    now = time.monotonic()
    with _LIVE_CACHE_LOCK:
        cached = _DB_READINESS_CACHE
        if not fresh and cached is not None and cached[0] > now:
            return cached[1], cached[2]
    with _DB_READINESS_LOCK:
        now = time.monotonic()
        with _LIVE_CACHE_LOCK:
            cached = _DB_READINESS_CACHE
            if not fresh and cached is not None and cached[0] > now:
                return cached[1], cached[2]
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            ready = bool(check_database_connection())
        except Exception:
            ready = False
            log.warning("database readiness check failed", exc_info=True)
        with _LIVE_CACHE_LOCK:
            _DB_READINESS_CACHE = (
                time.monotonic() + DB_READINESS_CACHE_TTL_SECONDS,
                ready,
                checked_at,
            )
        return ready, checked_at


def _live_item_availability_payload(restaurant_id: int, item_id: int):
    frame = fetch_restaurant_item_availability(restaurant_id, item_id)
    if frame.empty:
        raise HTTPException(status_code=404, detail="active menu item not found")
    raw = json.loads(frame.to_json(orient="records", force_ascii=False))
    if not raw or raw[0].get("item_id") is None:
        raise HTTPException(status_code=404, detail="active menu item not found")

    sizes = []
    item_availability = _availability_state(
        raw[0].get("availability_mode"),
        raw[0].get("current_availability_value"),
    )
    for row in raw:
        row_availability = _availability_state(
            row.get("availability_mode"),
            row.get("current_availability_value"),
        )
        if row.get("item_size_id") is None:
            item_availability = row_availability
            continue
        sizes.append({
            "item_size_id": int(row["item_size_id"]),
            "title_ar": row.get("size_ar") or "",
            "title_en": row.get("size_en") or "",
            "code": row.get("size_code") or "",
            "price": row.get("price"),
            "takeaway_price": row.get("takeaway_price"),
            **row_availability,
        })

    if sizes:
        available_size_count = sum(1 for size in sizes if size["is_available"])
        is_available = available_size_count > 0
        availability_reason = (
            "available_size_exists" if is_available else "all_sizes_unavailable"
        )
    else:
        available_size_count = 0
        is_available = item_availability["is_available"]
        availability_reason = item_availability["availability_reason"]

    payload = {
        "restaurant_id": int(restaurant_id),
        "item_id": int(item_id),
        "category_id": raw[0].get("category_id"),
        "is_available": is_available,
        "availability_reason": availability_reason,
        "available_size_count": available_size_count,
        "sizes": sizes,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": "live_database",
    }
    # A stock check is authoritative and may reveal a changed state. Drop any
    # short-lived menu snapshot so subsequent recommendations cannot reuse it.
    _invalidate_menu_cache(restaurant_id)
    return payload


_CONTEXT_META = {
    "based_on_cart": {
        "model_key": "full_cart",
        "label_ar": "السلة كاملة",
        "description_ar": "يربط جميع أصناف السلة معًا ويقترح ما يُطلب معها.",
        "score_label_ar": "قوة السلة",
        "business_type": "cross_sell",
        "score_scale": 0.30,
    },
    "based_on_last_item": {
        "model_key": "last_item",
        "label_ar": "آخر صنف",
        "description_ar": "يركز على الارتباط مع آخر صنف تمت إضافته.",
        "score_label_ar": "قوة الارتباط",
        "business_type": "cross_sell",
        "score_scale": 0.35,
    },
    "similar_alternatives": {
        "model_key": "similarity",
        "label_ar": "التشابه",
        "description_ar": "يستخدم تشابه سلوك الطلبات لاكتشاف خيارات مناسبة.",
        "score_label_ar": "درجة التشابه",
        "business_type": "similar_alternative",
        "score_scale": 0.35,
    },
    "popular": {
        "model_key": "restaurant_popularity",
        "label_ar": "الأكثر طلبًا",
        "description_ar": "يرتب الأصناف الأكثر طلبًا في المطعم.",
        "score_label_ar": "قوة الطلب",
        "business_type": "popular",
        "score_scale": 0.25,
    },
    "time_context": {
        "model_key": "time_aware_popularity",
        "label_ar": "الأكثر طلبًا في هذه الفترة",
        "description_ar": "يرتب الطلب الفعلي في فترة اليوم الحالية بتوقيت الرياض.",
        "score_label_ar": "قوة الطلب في الفترة",
        "business_type": "popular",
        "score_scale": 0.25,
    },
    "current_trend": {
        "model_key": "current_trend_momentum",
        "label_ar": "الترند الحالي",
        "description_ar": "يرصد تسارع الطلب الحديث مقارنة بنافذة سابقة؛ لا يتوقع المستقبل.",
        "score_label_ar": "زخم مرصود",
        "business_type": "current_trend",
        "score_scale": 1.0,
    },
}

_MODEL_ALLOWED_SOURCES = {
    "based_on_cart": {"restaurant_fbt", "pooled_fbt", "live_menu_fallback"},
    "based_on_last_item": {"restaurant_fbt", "pooled_fbt"},
    "similar_alternatives": {"item2vec"},
    "popular": {"restaurant_popularity", "global_common", "live_menu_fallback"},
    "time_context": {"time_based"},
    "current_trend": {"current_trend_nowcast"},
}

_MODEL_LABELS_AR = {
    "fbt_confidence": "الارتباط حسب الثقة",
    "fbt_hybrid": "الارتباط الهجين",
    "fbt_paircount": "الارتباط حسب تكرار الطلب",
    "fbt_lift": "الارتباط حسب قوة الرفع",
    "time_aware_popularity": "الأكثر طلبًا في هذه الفترة",
    "restaurant_popularity": "الأكثر طلبًا",
    "item2vec": "التشابه السلوكي",
    "pooled_fbt": "ارتباط المطاعم المشابهة",
    "live_menu_fallback": "استكشاف المنيو المتاح",
    "full_cart": "السلة كاملة",
    "last_item": "آخر صنف",
    "current_trend_momentum": "الترند الحالي المرصود",
    "user_affinity": "التخصيص حسب المستخدم",
}


def _recommendation_context(section_name: str, item: dict) -> str:
    # Section identity represents the strategy that generated the candidate.
    # ``source`` is supporting evidence and must not move an item to another model.
    if section_name in _CONTEXT_META:
        return section_name
    source = str(item.get("source") or "").lower()
    recommendation_type = str(item.get("recommendation_type") or "").lower()
    if source == "time_based" or section_name == "time_context":
        return "time_context"
    if source in {"restaurant_popularity", "global_common"} or recommendation_type == "popular":
        return "popular"
    if source == "item2vec" or recommendation_type == "similar_alternative":
        return "similar_alternatives"
    return "popular"


def _confidence_band_ar(percent: float) -> str:
    if percent >= 90:
        return "مطابقة ممتازة"
    if percent >= 80:
        return "مطابقة قوية"
    if percent >= 70:
        return "مناسبة جدًا"
    if percent >= 55:
        return "مناسبة"
    return "استكشافي"


def _calibrated_match_score(context: str, raw_score: float, max_score: float, rank: int) -> float:
    """Map heterogeneous ranking scores to a stable, bounded display score.

    This is deliberately called a match score rather than purchase probability.
    Each strategy has its own score scale; the transform is monotonic, capped at
    97%, and keeps a weak pool below the 70% recommendation threshold.
    """
    meta = _CONTEXT_META[context]
    safe_raw = max(0.0, float(raw_score or 0.0))
    scale = float(meta["score_scale"])
    base_signal = 1.0 - math.exp(-safe_raw / scale)
    relative_signal = min(safe_raw / max_score, 1.0) if max_score > 0 else 0.0
    rank_signal = max(0.0, 1.0 - (max(0, int(rank)) / 4.0))
    if context in {"popular", "time_context"}:
        # Popularity is meaningful relative to the restaurant's own catalog.
        # Present it as demand strength, not as a raw purchase probability.
        calibrated = 0.58 + (0.34 * math.sqrt(relative_signal)) + (0.05 * rank_signal)
        return float(round(max(1.0, min(calibrated * 100.0, 97.0))))
    calibrated = base_signal + (1.0 - base_signal) * (
        0.08 + 0.10 * relative_signal + 0.04 * rank_signal
    )
    return float(round(max(1.0, min(calibrated * 100.0, 97.0))))


def _validation_percent(record: dict | None) -> float | None:
    if not record or not record.get("validated"):
        return None
    if record.get("validation_percent") is not None:
        value = float(record["validation_percent"])
    elif record.get("validation_value") is not None:
        value = float(record["validation_value"]) * 100.0
    else:
        return None
    return round(max(0.0, min(value, 100.0)), 2)


def _supporting_validation_record(
    context: str,
    validation_catalog: dict,
    time_period_key: str | None,
) -> dict:
    if context == "popular":
        record = ((validation_catalog.get("empty_cart") or {}).get("restaurant_popularity") or {})
    elif context == "time_context":
        record = dict(
            ((validation_catalog.get("empty_cart") or {}).get("time_aware_popularity") or {})
        )
        if time_period_key:
            period = (record.get("by_time_period") or {}).get(time_period_key)
            if period:
                record.update(period)
        record["model_key"] = "time_aware_popularity"
    elif context == "similar_alternatives":
        record = ((validation_catalog.get("supporting_models") or {}).get("item2vec") or {})
    elif context == "current_trend":
        record = {
            "model_key": TREND_MODEL_KEY,
            "validated": False,
            "validation_metric": None,
            "validation_value": None,
            "validation_trials": 0,
            "validation_scope": "descriptive_live_nowcast",
            "validation_source": "live_database_7d_vs_prior_28d",
            "unavailable_reason": "not_a_forecast_or_probability_model",
        }
    else:
        record = {}
    return dict(record)


def _provenance_descriptor(
    context: str,
    item: dict,
    *,
    selected_model: dict | None,
    validation_catalog: dict,
    time_period_key: str | None,
    time_period_ar: str | None,
    selected: bool,
) -> dict:
    source = str(item.get("source") or "unknown")
    if context in {"popular", "time_context", "similar_alternatives", "current_trend"}:
        record = _supporting_validation_record(context, validation_catalog, time_period_key)
    elif selected:
        record = dict(selected_model or {})
    elif context in {"based_on_last_item", "based_on_cart"}:
        record = dict(selected_model or {})
    else:
        record = _supporting_validation_record(context, validation_catalog, time_period_key)

    if source in {"pooled_fbt", "live_menu_fallback", "global_common"}:
        record = {
            "model_key": source,
            "validated": False,
            "validation_metric": None,
            "validation_value": None,
            "validation_trials": 0,
            "validation_scope": "unvalidated_fallback",
            "validation_source": None,
        }
    model_key = str(record.get("model_key") or _CONTEXT_META[context]["model_key"])
    descriptor = {
        "model_key": model_key,
        "label_ar": record.get("label_ar") or _MODEL_LABELS_AR.get(model_key, model_key),
        "context_key": context,
        "role": "selected" if selected else "supporting",
        "source": source,
        "validated": bool(record.get("validated")),
        "validation_metric": record.get("validation_metric"),
        "validation_percent": _validation_percent(record),
        "validation_trials": max(0, int(record.get("validation_trials") or 0)),
        "validation_scope": record.get("validation_scope"),
        "validation_source": record.get("validation_source"),
        "evaluation_version": (
            record.get("evaluation_version") or validation_catalog.get("evaluation_version")
        ),
        "time_period_key": time_period_key if context in {"time_context", "current_trend"} else None,
        "time_period_ar": time_period_ar if context in {"time_context", "current_trend"} else None,
        "unavailable_reason": record.get("unavailable_reason"),
    }
    return descriptor


def _source_label_ar(context: str, time_period_ar: str | None) -> str:
    if context == "time_context":
        return f"الأكثر طلبًا في فترة {time_period_ar}" if time_period_ar else "الأكثر طلبًا حسب الوقت"
    if context == "current_trend":
        return f"ترند حالي مرصود في فترة {time_period_ar}" if time_period_ar else "ترند حالي مرصود"
    return {
        "based_on_cart": "حسب السلة كاملة",
        "based_on_last_item": "حسب آخر صنف",
        "similar_alternatives": "حسب التشابه السلوكي",
        "popular": "الأكثر طلبًا",
    }.get(context, context)


def _annotate_provenance(
    item: dict,
    selected_context: str,
    support_by_context: dict[str, dict[int, dict]],
    *,
    selected_model: dict | None,
    validation_catalog: dict,
    time_period_key: str | None,
    time_period_ar: str | None,
) -> dict:
    item_id = int(item["item_id"])
    contexts = [selected_context]
    for context in (
        "based_on_cart",
        "based_on_last_item",
        "similar_alternatives",
        "popular",
        "time_context",
        "current_trend",
    ):
        if context != selected_context and item_id in support_by_context.get(context, {}):
            contexts.append(context)

    descriptors = []
    seen = set()
    for context in contexts:
        support_item = support_by_context.get(context, {}).get(item_id, item)
        descriptor = _provenance_descriptor(
            context,
            support_item,
            selected_model=selected_model,
            validation_catalog=validation_catalog,
            time_period_key=time_period_key,
            time_period_ar=time_period_ar,
            selected=context == selected_context,
        )
        key = (descriptor["model_key"], descriptor["context_key"])
        if key in seen:
            continue
        seen.add(key)
        descriptors.append(descriptor)

    selected_descriptor = descriptors[0]
    supporting = descriptors[1:]
    labels = [_source_label_ar(context, time_period_ar) for context in contexts]
    annotated = {
        **item,
        "model_key": selected_descriptor["model_key"],
        "model_label_ar": selected_descriptor["label_ar"],
        "selected_model": selected_descriptor,
        "supporting_models": supporting,
        "contributing_models": descriptors,
        "source_labels_ar": labels,
        "model_accuracy_percent": selected_descriptor["validation_percent"],
        "accuracy_metric": selected_descriptor["validation_metric"],
        "model_accuracy_metric": selected_descriptor["validation_metric"],
        "accuracy_validated": selected_descriptor["validated"],
        "time_period_key": time_period_key if {"time_context", "current_trend"}.intersection(contexts) else None,
        "time_period_ar": time_period_ar if {"time_context", "current_trend"}.intersection(contexts) else None,
        "model_agreement_count": len(descriptors),
    }
    if supporting:
        support_text = " + ".join(label for label in labels[1:] if label)
        if support_text:
            annotated["reason"] = " · ".join(
                part for part in [str(item.get("reason") or "").strip(), f"مدعوم أيضًا بـ {support_text}"]
                if part
            )
    return annotated


def _apply_live_recommendation_rules(
    result: dict,
    live_menu: dict,
    cart_item_ids,
    last_added_item_id=None,
    previous_top_item_id=None,
    display_limit: int = 5,
    validation_catalog: dict | None = None,
    item_groups: dict[int, int] | None = None,
    customer_id: int | None = None,
):
    live_items = {int(item["item_id"]): item for item in live_menu["items"]}
    item_groups = {int(key): int(value) for key, value in (item_groups or {}).items()}
    cart_list = _unique_positive_ints(cart_item_ids)
    cart_ids = set(cart_list)
    has_cart = bool(cart_ids)
    last_item_id = int(last_added_item_id) if last_added_item_id else None
    if last_item_id not in cart_ids:
        last_item_id = cart_list[-1] if cart_list else None
    blocked_category_id = (
        live_items.get(last_item_id, {}).get("category_id")
        if last_item_id is not None
        else None
    )
    previous_top = int(previous_top_item_id) if previous_top_item_id else None
    pools = {key: [] for key in _CONTEXT_META}
    pool_seen = {key: set() for key in _CONTEXT_META}
    skipped = {
        "previous_top_excluded": 0,
        "not_live": 0,
        "out_of_stock": 0,
        "already_in_cart": 0,
        "same_category_as_last_item": 0,
        "popular_after_cart": 0,
        "time_only_after_cart": 0,
        "model_source_mismatch": 0,
        "duplicate_common_item": 0,
    }

    input_sections = {
        key: list(value or []) for key, value in (result.get("sections") or {}).items()
    }
    for key, value in (result.get("supporting_sections") or {}).items():
        input_sections.setdefault(key, []).extend(value or [])
    for section_name, section_items in input_sections.items():
        for raw_item in section_items or []:
            item_id = int(raw_item["item_id"])
            if previous_top is not None and item_id == previous_top:
                skipped["previous_top_excluded"] += 1
                continue
            live_item = live_items.get(item_id)
            if live_item is None:
                skipped["not_live"] += 1
                continue
            if not live_item.get("is_available", True):
                skipped["out_of_stock"] += 1
                continue
            if item_id in cart_ids:
                skipped["already_in_cart"] += 1
                continue

            context = _recommendation_context(section_name, raw_item)
            category_id = live_item.get("category_id")
            if (
                has_cart
                and blocked_category_id is not None
                and category_id == blocked_category_id
            ):
                skipped["same_category_as_last_item"] += 1
                continue

            source = str(raw_item.get("source") or "").lower()
            if source not in _MODEL_ALLOWED_SOURCES[context]:
                skipped["model_source_mismatch"] += 1
                continue
            title_key = " ".join(
                str(live_item.get("title_ar") or live_item.get("title_en") or "").casefold().split()
            )
            common_group_id = item_groups.get(item_id)
            identity = (
                ("group", common_group_id)
                if common_group_id is not None
                else (("title", title_key) if title_key else ("item", item_id))
            )
            if identity in pool_seen[context]:
                skipped["duplicate_common_item"] += 1
                continue
            pool_seen[context].add(identity)
            meta = _CONTEXT_META[context]
            pools[context].append({
                **raw_item,
                "category_id": category_id,
                "recommendation_context": context,
                "recommendation_type": meta["business_type"],
                "type_label_ar": meta["label_ar"],
                "model_key": meta["model_key"],
                "model_label_ar": meta["label_ar"],
                "score_label_ar": meta["score_label_ar"],
                "is_available": True,
                "availability_reason": live_item.get("availability_reason"),
            })

    for context, items in pools.items():
        items.sort(key=lambda item: (-float(item.get("score") or 0.0), int(item["item_id"])))
        safe_scores = [max(0.0, float(item.get("score") or 0.0)) for item in items]
        max_score = max(safe_scores, default=0.0)
        for rank, (item, raw_score) in enumerate(zip(items, safe_scores), start=1):
            if context == "current_trend":
                item["rank"] = rank
                item["compatibility_percent"] = None
                item["meets_threshold"] = True
                item["confidence_band_ar"] = "رصد وصفي"
                item["probability_percent"] = None
                continue
            compatibility = _calibrated_match_score(context, raw_score, max_score, rank - 1)
            item["rank"] = rank
            item["compatibility_percent"] = compatibility
            item["meets_threshold"] = compatibility >= 70.0
            item["confidence_band_ar"] = _confidence_band_ar(compatibility)
            # The packaged artifact has ranking scores but no temporal probability
            # calibrator, so do not present these values as purchase probabilities.
            item["probability_percent"] = None

    max_results = max(1, min(int(display_limit or 5), 5))
    visible_pools = {}
    strong_pools = {}
    for context, items in pools.items():
        strong = [item for item in items if item.get("meets_threshold")]
        strong_pools[context] = strong[:max_results]
        visible_pools[context] = (strong or items)[:max_results]

    selected_model = dict(result.get("selected_model") or {})
    support_by_context = {
        context: {int(item["item_id"]): item for item in items}
        for context, items in pools.items()
    }
    validation_catalog = dict(validation_catalog or {})
    cart_catalog = dict(validation_catalog.get("cart") or {})
    route_cart_model = selected_model if has_cart else dict(
        (cart_catalog.get("by_restaurant") or {}).get(str(result.get("restaurant_id")))
        or cart_catalog.get("global")
        or {}
    )
    time_period_key = result.get("time_period_key")
    time_period_ar = result.get("time_period_ar")

    def annotate_items(context: str, items: list[dict]) -> list[dict]:
        return [
            _annotate_provenance(
                item,
                context,
                support_by_context,
                selected_model=(
                    route_cart_model if context in {"based_on_cart", "based_on_last_item"} else None
                ),
                validation_catalog=validation_catalog,
                time_period_key=time_period_key,
                time_period_ar=time_period_ar,
            )
            for item in items[:max_results]
        ]

    full_cart_items = annotate_items("based_on_cart", visible_pools["based_on_cart"])
    last_item_items = annotate_items("based_on_last_item", visible_pools["based_on_last_item"])
    popularity_context = "time_context" if visible_pools["time_context"] else "popular"
    popularity_items = annotate_items(popularity_context, visible_pools[popularity_context])
    current_trend_items = annotate_items("current_trend", visible_pools["current_trend"])

    def descriptor_for(context: str, items: list[dict], source: str) -> dict:
        if items and items[0].get("selected_model"):
            return dict(items[0]["selected_model"])
        return _provenance_descriptor(
            context,
            {"source": source},
            selected_model=(
                route_cart_model if context in {"based_on_cart", "based_on_last_item"} else None
            ),
            validation_catalog=validation_catalog,
            time_period_key=time_period_key,
            time_period_ar=time_period_ar,
            selected=True,
        )

    full_descriptor = descriptor_for("based_on_cart", full_cart_items, "restaurant_fbt")
    last_descriptor = descriptor_for("based_on_last_item", last_item_items, "restaurant_fbt")
    popularity_source = "time_based" if popularity_context == "time_context" else "restaurant_popularity"
    popularity_descriptor = descriptor_for(popularity_context, popularity_items, popularity_source)
    trend_descriptor = descriptor_for("current_trend", current_trend_items, "current_trend_nowcast")

    def group_payload(
        *,
        context_key: str,
        internal_context: str,
        descriptor: dict,
        suggestions: list[dict],
        status: str,
        why_ar: str,
        unavailable_reason: str | None,
        selected: bool,
        future_ready: bool = False,
        data_as_of: str | None = None,
        latest_order_at: str | None = None,
        freshness_status: str | None = None,
    ) -> dict:
        return {
            "context_key": context_key,
            "model_key": descriptor.get("model_key") or _CONTEXT_META[internal_context]["model_key"],
            "label_ar": descriptor.get("label_ar") or _CONTEXT_META[internal_context]["label_ar"],
            "description_ar": _CONTEXT_META[internal_context]["description_ar"],
            "available": bool(suggestions),
            "threshold_fallback_used": bool(
                suggestions and internal_context != "current_trend" and not strong_pools[internal_context]
            ),
            "suggestions": suggestions,
            "selected": selected,
            "validated": bool(descriptor.get("validated")),
            "validation_metric": descriptor.get("validation_metric"),
            "validation_percent": descriptor.get("validation_percent"),
            "validation_trials": max(0, int(descriptor.get("validation_trials") or 0)),
            "validation_scope": descriptor.get("validation_scope"),
            "evaluation_version": descriptor.get("evaluation_version"),
            "status": status,
            "why_ar": why_ar,
            "unavailable_reason": unavailable_reason,
            "selected_model": descriptor,
            "future_ready": future_ready,
            "data_as_of": data_as_of,
            "latest_order_at": latest_order_at,
            "freshness_status": freshness_status,
        }

    full_fallback = bool(
        full_cart_items
        and (
            not full_descriptor.get("validated")
            or any(item.get("source") in {"pooled_fbt", "live_menu_fallback"} for item in full_cart_items)
        )
    )
    if not has_cart:
        full_status, full_reason = "unavailable", "empty_cart"
        full_why = "يحتاج مسار السلة الكاملة إلى صنف واحد على الأقل في السلة."
    elif not full_cart_items:
        full_status, full_reason = "unavailable", "no_eligible_full_cart_associations"
        full_why = "لم توجد ارتباطات صالحة ومتاحة لكل أصناف السلة الحالية."
    elif full_fallback:
        full_status, full_reason = "fallback", "unvalidated_cart_fallback"
        full_why = "عُرض أفضل بديل آمن متاح لأن مودل ارتباط السلة الموثق لم يُنتج نتائج صالحة."
    else:
        full_status, full_reason = "available", None
        full_why = "اقتراحات مرتبطة بالسلة كاملة من المودل الأعلى توثيقًا لهذا المطعم."

    last_fallback = bool(
        last_item_items
        and (
            not last_descriptor.get("validated")
            or any(item.get("source") == "pooled_fbt" for item in last_item_items)
        )
    )
    if not has_cart or last_item_id is None:
        last_status, last_reason = "unavailable", "last_item_required"
        last_why = "يحتاج مسار آخر صنف إلى صنف مضاف في السلة."
    elif not last_item_items:
        last_status, last_reason = "unavailable", "no_eligible_last_item_associations"
        last_why = "لا توجد ارتباطات متاحة وآمنة مع آخر صنف حاليًا."
    elif last_fallback:
        last_status, last_reason = "fallback", "unvalidated_last_item_fallback"
        last_why = "استخدم المسار ارتباطًا احتياطيًا لعدم توفر ارتباط محلي موثق مع آخر صنف."
    else:
        last_status, last_reason = "available", None
        last_why = "اقتراحات مرتبطة بآخر صنف أضيف إلى السلة."

    if not popularity_items:
        popularity_status, popularity_reason = "unavailable", "no_eligible_popularity_items"
        popularity_why = "لا توجد أصناف شعبية متاحة وآمنة للعرض حاليًا."
    elif popularity_context == "popular":
        popularity_status, popularity_reason = "fallback", "time_period_popularity_unavailable"
        popularity_why = "لا تتوفر عينة كافية للفترة الحالية؛ عُرضت شعبية المطعم العامة."
    else:
        popularity_status, popularity_reason = "available", None
        popularity_why = (
            f"الأصناف الأكثر طلبًا في فترة {time_period_ar} بتوقيت الرياض."
            if time_period_ar else "الأصناف الأكثر طلبًا في الفترة الحالية."
        )

    trend_meta = dict(result.get("current_trend") or {})
    trend_status = str(trend_meta.get("status") or "unavailable")
    trend_reason = trend_meta.get("unavailable_reason")
    trend_why = str(trend_meta.get("why_ar") or "لا تتوفر بيانات ترند حالي موثوقة.")
    if trend_status in {"available", "fallback"} and not current_trend_items:
        trend_status = "unavailable"
        trend_reason = "no_eligible_live_trend_items"
        trend_why = "تم رصد حركة حديثة، لكن جميع أصنافها غير متاحة أو محجوبة بقواعد السلامة."

    user_descriptor = {
        "model_key": "user_affinity",
        "label_ar": _MODEL_LABELS_AR["user_affinity"],
        "context_key": "user",
        "role": "selected",
        "source": None,
        "validated": False,
        "validation_metric": None,
        "validation_percent": None,
        "validation_trials": 0,
        "validation_scope": "unavailable",
        "validation_source": None,
        "evaluation_version": validation_catalog.get("evaluation_version"),
        "time_period_key": None,
        "time_period_ar": None,
        "unavailable_reason": (
            "customer_identifier_not_provided"
            if customer_id is None
            else "insufficient_validated_customer_order_linkage"
        ),
    }
    user_reason = user_descriptor["unavailable_reason"]
    user_why = (
        "مسار المستخدم جاهز في عقد الـAPI، لكنه يحتاج معرف مستخدم مجهولًا وتاريخ تفاعل صالحًا."
        if customer_id is None
        else "تم استلام معرف المستخدم، لكن تغطية الربط مع الطلبات غير كافية لتفعيل مودل شخصي موثق."
    )

    default_context_key = "full_cart" if has_cart else "popularity"
    models = [
        group_payload(
            context_key="full_cart", internal_context="based_on_cart",
            descriptor=full_descriptor, suggestions=full_cart_items,
            status=full_status, why_ar=full_why, unavailable_reason=full_reason,
            selected=default_context_key == "full_cart",
        ),
        group_payload(
            context_key="last_item", internal_context="based_on_last_item",
            descriptor=last_descriptor, suggestions=last_item_items,
            status=last_status, why_ar=last_why, unavailable_reason=last_reason,
            selected=False,
        ),
        group_payload(
            context_key="popularity", internal_context=popularity_context,
            descriptor=popularity_descriptor, suggestions=popularity_items,
            status=popularity_status, why_ar=popularity_why,
            unavailable_reason=popularity_reason,
            selected=default_context_key == "popularity",
        ),
        group_payload(
            context_key="current_trend", internal_context="current_trend",
            descriptor=trend_descriptor, suggestions=current_trend_items,
            status=trend_status, why_ar=trend_why, unavailable_reason=trend_reason,
            selected=False,
            data_as_of=trend_meta.get("data_as_of"),
            latest_order_at=trend_meta.get("latest_order_at"),
            freshness_status=trend_meta.get("freshness_status"),
        ),
        {
            "context_key": "user",
            "model_key": "user_affinity",
            "label_ar": _MODEL_LABELS_AR["user_affinity"],
            "description_ar": "مسار مخصص لتفضيلات المستخدم عند توفر معرف وتاريخ تفاعل موثوقين.",
            "available": False,
            "threshold_fallback_used": False,
            "suggestions": [],
            "selected": False,
            "validated": False,
            "validation_metric": None,
            "validation_percent": None,
            "validation_trials": 0,
            "validation_scope": "unavailable",
            "evaluation_version": validation_catalog.get("evaluation_version"),
            "status": "unavailable",
            "why_ar": user_why,
            "unavailable_reason": user_reason,
            "selected_model": user_descriptor,
            "future_ready": True,
            "data_as_of": None,
            "latest_order_at": None,
            "freshness_status": None,
        },
    ]

    default_group = models[0] if has_cart else models[2]
    top_recommendations = list(default_group["suggestions"])
    threshold_fallback_used = bool(default_group["threshold_fallback_used"])
    selected_descriptor = default_group.get("selected_model")
    default_model_key = str(default_group["model_key"])

    if has_cart:
        selected_ids = {int(item["item_id"]) for item in top_recommendations}
        skipped["popular_after_cart"] += sum(
            1 for item in pools["popular"] if int(item["item_id"]) not in selected_ids
        )
        skipped["time_only_after_cart"] += sum(
            1 for item in pools["time_context"] if int(item["item_id"]) not in selected_ids
        )

    warnings = list(result.get("warnings") or [])
    for reason, count in skipped.items():
        if count:
            warnings.append(f"{reason}:{count}")
    if threshold_fallback_used:
        warnings.append("priority_model_below_70_showing_best_available")
    if not top_recommendations:
        warnings.append("no_eligible_live_recommendations")
        if previous_top is not None:
            warnings.append("no_alternative_after_rotation")

    result["sections"] = pools
    result["top_recommendations"] = top_recommendations
    result["models"] = models
    result["default_model_key"] = default_model_key
    result["default_context_key"] = default_context_key
    result["available_model_keys"] = list(dict.fromkeys(
        model["model_key"] for model in models if model["status"] != "unavailable"
    ))
    result["available_context_keys"] = [
        model["context_key"] for model in models if model["status"] != "unavailable"
    ]
    result["warnings"] = warnings
    result["threshold_percent"] = 70.0
    result["threshold_fallback_used"] = threshold_fallback_used
    result["selected_model"] = selected_descriptor or selected_model or None
    supporting_models = []
    supporting_seen = set()
    for item in top_recommendations:
        for descriptor in item.get("supporting_models") or []:
            key = (descriptor.get("model_key"), descriptor.get("context_key"))
            if key in supporting_seen:
                continue
            supporting_seen.add(key)
            supporting_models.append(descriptor)
    result["supporting_models"] = supporting_models
    result["unavailable_models"] = [user_descriptor]
    result["selection_policy"] = (
        result.get("selection_policy")
        or validation_catalog.get("selection_policy")
        or "deterministic_selected_model_without_score_blending"
    )
    result["fallback_used"] = bool(
        result.get("fallback_used")
        or default_group["status"] in {"fallback", "unavailable"}
        or threshold_fallback_used
        or not top_recommendations
    )
    result["customer_id"] = int(customer_id) if customer_id is not None else None
    return result


def _build_widget_recommendations(req: WidgetRecommendationRequest, request: Request):
    t0 = time.perf_counter()
    # Load the active menu first so the portable model can serve restaurants that
    # were not present (or had too little history) when the artifact was trained.
    # The same short-lived snapshot is reused by the final stock/category filter.
    live_menu = _live_menu_payload(
        req.restaurant_id,
        include_inactive=False,
        fresh=False,
    )
    model = load_model()
    result = model.recommend(
        restaurant_id=req.restaurant_id,
        cart_item_ids=list(req.cart_item_ids),
        last_added_item_id=req.last_added_item_id,
        limit=req.limit,
        previous_top_item_id=req.previous_top_item_id,
        live_candidates=live_menu["items"],
        context=dict(req.context or {}),
    )
    trend_payload = _current_trend_payload(
        req.restaurant_id,
        result.get("time_period_key"),
        result.get("time_period_ar"),
        model,
    )
    result.setdefault("sections", {})["current_trend"] = list(trend_payload.get("items") or [])
    result["current_trend"] = trend_payload
    result = _apply_live_recommendation_rules(
        result,
        live_menu,
        req.cart_item_ids,
        last_added_item_id=req.last_added_item_id,
        previous_top_item_id=req.previous_top_item_id,
        display_limit=req.limit,
        validation_catalog=dict(model.metadata.get("validated_model_selection") or {}),
        item_groups=model.item_groups,
        customer_id=req.customer_id,
    )
    latency_ms = round(1000 * (time.perf_counter() - t0), 2)
    result["request_id"] = request.state.request_id
    result["latency_ms"] = latency_ms
    return result


def _filter_legacy_result_against_live_menu(result: dict, restaurant_id: int) -> dict:
    """Keep compatibility endpoints from returning deleted or depleted items."""
    live_menu = _live_menu_payload(int(restaurant_id), include_inactive=False, fresh=False)
    available_ids = {
        int(item["item_id"])
        for item in live_menu["items"]
        if item.get("is_available", False)
    }
    filtered = dict(result)
    filtered["recommendations"] = [
        item for item in result.get("recommendations", [])
        if int(item.get("item_id")) in available_ids
    ]
    filtered_groups = []
    for group in result.get("recommendation_groups", []):
        filtered_groups.append({
            **group,
            "items": [
                item for item in group.get("items", [])
                if int(item.get("item_id")) in available_ids
            ],
        })
    filtered["recommendation_groups"] = filtered_groups
    filtered["fallback_used"] = bool(filtered.get("fallback_used") or not filtered["recommendations"])
    return filtered


def _health_payload():
    model = load_model()
    eng = model.engine
    disabled, reason = kill_switch_status()
    database_ready, readiness_checked_at = _database_readiness()
    recall = (model.metadata.get("eval_recall@5") or {}).get("hybrid_production")
    accuracy_percent = None
    if recall is not None:
        accuracy_percent = round(max(0.0, min(float(recall), 1.0)) * 100, 2)
    return HealthResponse(
        status="ok" if database_ready else "not_ready",
        model_version=model.model_version,
        last_trained_at=_META.get("generated_at"),
        model_accuracy_metric="eval_recall@5.hybrid_production",
        model_accuracy_percent=accuracy_percent,
        restaurants_with_fbt=len(eng.restaurants_with_fbt),
        restaurants_with_popularity=len(eng.restaurants_with_pop),
        kill_switch_active=disabled,
        kill_switch_reason=reason or None,
        api_key_required=api_key_required(),
        database_ready=database_ready,
        readiness_checked_at=readiness_checked_at,
    )


@app.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
def health():
    payload = _health_payload()
    if not payload.database_ready:
        return JSONResponse(status_code=503, content=payload.model_dump(mode="json"))
    return payload


@app.get("/ready", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
def readiness():
    """Readiness alias suitable for platforms that separate live/ready probes."""
    return health()


@app.get("/metrics")
def metrics():
    snap = METRICS.snapshot()
    disabled, reason = kill_switch_status()
    snap["kill_switch_active"] = disabled
    snap["model_version"] = MODEL_VERSION
    return snap


@app.post("/recommendations", response_model=RecommendationResponse)
def recommendations(req: RecommendationRequest, request: Request, x_api_key: str = Header(default=None)):
    _guard(request, x_api_key, req.restaurant_id)
    if len(req.cart_item_ids) > MAX_CART:
        raise HTTPException(status_code=400, detail=f"cart too large (>{MAX_CART})")
    eng = get_engine()
    res = eng.recommend_groups(
        restaurant_id=req.restaurant_id,
        cart_item_ids=req.cart_item_ids,
        customer_id=req.customer_id,
        top_k=req.top_k,
        include_types=req.include_types,
        context=req.context.model_dump() if req.context else None,
    )
    res = _filter_legacy_result_against_live_menu(res, req.restaurant_id)
    res["request_id"] = request.state.request_id
    if not res.get("recommendations"):
        METRICS.record_rejected()
    return res


@app.post("/api/v1/recommendations", response_model=WidgetRecommendationResponse)
@app.post("/api/recommendations", response_model=WidgetRecommendationResponse)
def widget_recommendations(req: WidgetRecommendationRequest, request: Request,
                           x_api_key: str = Header(default=None)):
    _guard(request, x_api_key, req.restaurant_id)
    if len(req.cart_item_ids) > MAX_CART:
        raise HTTPException(status_code=400, detail=f"cart too large (>{MAX_CART})")
    return _build_widget_recommendations(req, request)


@app.get("/restaurants/{restaurant_id}/popular", response_model=RecommendationResponse)
def popular(restaurant_id: int, request: Request, top_k: int = Query(10, ge=1, le=50),
            x_api_key: str = Header(default=None)):
    _guard(request, x_api_key, restaurant_id)
    eng = get_engine()
    live_menu = _live_menu_payload(int(restaurant_id), include_inactive=False, fresh=False)
    available_ids = {
        int(item["item_id"])
        for item in live_menu["items"]
        if item.get("is_available", False)
    }
    recs = [
        item for item in eng.popular(restaurant_id, top_k=top_k * 4)
        if int(item.get("item_id")) in available_ids
    ][:top_k]
    return RecommendationResponse(
        restaurant_id=restaurant_id, customer_id=None, recommendations=recs,
        fallback_used=True, model_version=MODEL_VERSION, request_id=request.state.request_id)


@app.get("/customers/{customer_id}/recommendations", response_model=RecommendationResponse)
def customer_recs(customer_id: int, request: Request,
                  restaurant_id: int = Query(..., ge=1), top_k: int = Query(10, ge=1, le=50),
                  x_api_key: str = Header(default=None)):
    _guard(request, x_api_key, restaurant_id)
    eng = get_engine()
    res = eng.for_customer(customer_id, restaurant_id, top_k=top_k)
    res = _filter_legacy_result_against_live_menu(res, restaurant_id)
    res["request_id"] = request.state.request_id
    return res


@app.post("/recommendation-events", response_model=EventAck)
@app.post("/api/v1/recommendation-events", response_model=EventAck)
@app.post("/api/recommendation-events", response_model=EventAck)
def record_event(ev: RecommendationEvent, request: Request, x_api_key: str = Header(default=None)):
    # Keep event logging available during kill switch periods while enforcing auth/rate limits.
    if not check_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    if not rate_limit_ok(_client_rate_key(request), scope="events"):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    if not tenant_allowed(x_api_key, ev.restaurant_id):
        raise HTTPException(status_code=403, detail="restaurant not allowed for this API key")
    # Validate against the current active catalog, not the training artifact. This
    # also supports restaurants and new items introduced after model training.
    live_menu = _live_menu_payload(int(ev.restaurant_id), include_inactive=False, fresh=False)
    restaurant_items = {int(item["item_id"]) for item in live_menu["items"]}
    if int(ev.recommended_item_id) not in restaurant_items:
        raise HTTPException(status_code=422, detail="recommended item does not belong to restaurant")
    invalid_cart_items = sorted(set(int(item_id) for item_id in ev.cart_item_ids) - restaurant_items)
    if invalid_cart_items:
        raise HTTPException(status_code=422, detail="cart contains items outside restaurant")
    row = ev.model_dump()
    if not row.get("request_id"):
        row["request_id"] = request.state.request_id
    if not row.get("timestamp"):
        row["timestamp"] = datetime.now(timezone.utc).isoformat()
    row["received_at"] = datetime.now(timezone.utc).isoformat()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = EVENTS_DIR / f"events-{day}.jsonl"
    try:
        encoded_row = json.dumps(row, ensure_ascii=False) + "\n"
        with _EVENTS_WRITE_LOCK:
            current_size = path.stat().st_size if path.exists() else 0
            if current_size + len(encoded_row.encode("utf-8")) > EVENTS_MAX_BYTES_PER_DAY:
                raise HTTPException(status_code=507, detail="daily event storage limit reached")
            with open(path, "a", encoding="utf-8") as f:
                f.write(encoded_row)
    except HTTPException:
        raise
    except Exception:
        log.exception("request_id=%s failed to persist event", request.state.request_id)
        raise HTTPException(status_code=500, detail="failed to store event")
    return EventAck(status="ok", stored=1)


@app.get("/ai-demo", response_class=HTMLResponse)
@app.get("/demo/ai-lab", response_class=HTMLResponse)
def ai_demo():
    return HTMLResponse(DEMO_HTML)


@app.get("/")
def root_demo():
    if LOVABLE_MENU_INDEX.exists():
        return FileResponse(LOVABLE_MENU_INDEX, media_type="text/html")
    return RedirectResponse(url="/try", status_code=307)


@app.get("/try", response_class=HTMLResponse)
@app.get("/demo/restaurant-menu", response_class=HTMLResponse)
@app.get("/restaurant-demo", response_class=HTMLResponse)
def restaurant_menu_demo():
    return HTMLResponse(CLEAN_MENU_APP_HTML)


@app.get("/demo/widget-integration")
def widget_integration_demo():
    path = WIDGET_DIR / "example-integration.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="widget integration demo not found")
    return FileResponse(path, media_type="text/html")


@app.get("/demo/widget/{asset_name}")
def widget_asset(asset_name: str):
    if asset_name not in {"smart-suggestions-widget.js", "smart-suggestions-widget.css"}:
        raise HTTPException(status_code=404, detail="widget asset not found")
    path = WIDGET_DIR / asset_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="widget asset not found")
    media_type = "text/javascript" if asset_name.endswith(".js") else "text/css"
    return FileResponse(path, media_type=media_type)


@app.get("/demo/smart-suggestions-widget.js")
def widget_script_alias():
    return widget_asset("smart-suggestions-widget.js")


@app.get("/demo/smart-suggestions-widget.css")
def widget_style_alias():
    return widget_asset("smart-suggestions-widget.css")


@app.get("/demo/final-delivery", response_class=HTMLResponse)
@app.get("/demo/final-delivery/", response_class=HTMLResponse)
def final_delivery_demo():
    path = FINAL_DELIVERY_DEMO if FINAL_DELIVERY_DEMO.exists() else LEGACY_FINAL_DELIVERY_DEMO
    if not path.exists():
        raise HTTPException(status_code=404, detail="final delivery demo not found")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/demo/restaurants/{restaurant_id}/menu")
def demo_restaurant_menu(restaurant_id: int):
    # Demo menu uses only local artifacts; it does not connect to production DB.
    eng = get_engine()
    return eng.menu(restaurant_id)


@app.get("/demo/restaurants")
def demo_restaurants(request: Request):
    """Restaurants with a current menu and at least one active item."""
    _guard_public_read(request)
    return _cached_restaurants_payload()


@app.get("/demo/restaurants/{restaurant_id}/live-menu")
def demo_live_restaurant_menu(
    restaurant_id: int,
    request: Request,
    include_inactive: bool = False,
):
    """Public demo view of the active menu only."""
    _guard_public_read(request)
    if include_inactive:
        raise HTTPException(status_code=403, detail="inactive menu items are not public")
    live_menu = _live_menu_payload(restaurant_id, include_inactive=False)
    records = [dict(item) for item in live_menu["items"]]
    # UI-compatible fields. Signals are filled by the model only when it knows the item.
    for item in records:
        item.update({"has_cross_sell": False, "has_similar_alternatives": False, "popularity_rank": None})
    categories = {}
    for item in records:
        category_id = item.get("category_id")
        key = str(category_id) if category_id != "" else "uncategorized"
        categories[key] = categories.get(key, 0) + 1
    restaurant = live_menu["restaurant"]
    return {
        "restaurant_id": int(restaurant_id),
        "restaurant_name": restaurant.get("name_ar") or restaurant.get("name") or f"Restaurant {restaurant_id}",
        "items": records,
        "categories": [{"category_id": key, "category": f"الفئة {key}", "count": count} for key, count in categories.items()],
        "default_cart_item_ids": [records[0]["item_id"]] if records else [],
        "source": "live_database",
        "include_inactive": False,
    }


@app.get("/api/menu/restaurants")
def menu_restaurants(request: Request, fresh: bool = False):
    """Live restaurant selector for the Lovable menu project (read-only)."""
    _guard_public_read(request, fresh=fresh)
    return _cached_restaurants_payload(fresh=fresh)


@app.get("/api/menu/restaurants/{restaurant_id}/items")
def menu_items(
    restaurant_id: int,
    request: Request,
    include_inactive: bool = False,
    fresh: bool = False,
):
    """Live menu from the DB, grouped per item with its available sizes/prices."""
    _guard_public_read(request, fresh=fresh)
    if include_inactive:
        raise HTTPException(status_code=403, detail="inactive menu items are not public")
    return _live_menu_payload(restaurant_id, include_inactive=False, fresh=fresh)


@app.get("/api/menu/restaurants/{restaurant_id}/items/{item_id}/availability")
def menu_item_availability(restaurant_id: int, item_id: int, request: Request):
    """Fresh, lightweight stock check for one item before accepting it."""
    _guard_public_read(request, fresh=True, rate_scope="availability")
    return _live_item_availability_payload(restaurant_id, item_id)

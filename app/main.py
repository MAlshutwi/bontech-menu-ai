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
import logging
import math
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
from .db import (fetch_restaurants, fetch_restaurants_with_menu_counts,
                 fetch_restaurant_menu, fetch_restaurant_menu_with_sizes,
                 fetch_restaurant_item_availability)
from .runtime import (kill_switch_status, check_api_key, api_key_required, tenant_allowed,
                      rate_limit_ok, new_request_id, METRICS)
from .demo_page import DEMO_HTML
from .clean_menu_page import CLEAN_MENU_APP_HTML
from .schemas import (RecommendationRequest, RecommendationResponse, HealthResponse,
                      RecommendationEvent, EventAck, WidgetRecommendationRequest,
                      WidgetRecommendationResponse)

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
_LIVE_CACHE_LOCK = RLock()
_RESTAURANTS_LOAD_LOCK = Lock()
_RESTAURANTS_CACHE = None
_MENU_CACHE = {}
_MENU_LOAD_LOCKS = {}


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
            log.warning("API auth disabled (serving.require_api_key=false) — internal network only")
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
    try:
        resp = await call_next(request)
    except Exception:
        latency = 1000 * (time.perf_counter() - t0)
        METRICS.record(request.url.path, latency, 500)
        log.exception("request_id=%s %s %s -> 500 (%.1fms)", rid, request.method, request.url.path, latency)
        return JSONResponse(status_code=500, content={"detail": "internal error", "request_id": rid})
    latency = 1000 * (time.perf_counter() - t0)
    METRICS.record(request.url.path, latency, resp.status_code)
    resp.headers["X-Request-Id"] = rid
    # Structured request log without PII or credentials.
    log.info("request_id=%s %s %s -> %s (%.1fms)", rid, request.method, request.url.path, resp.status_code, latency)
    return resp


def _guard(request: Request, x_api_key, restaurant_id=None):
    """Shared guard for kill switch, API key, rate limit, and tenant scope."""
    disabled, reason = kill_switch_status()
    if disabled:
        METRICS.disabled_hits += 1
        raise HTTPException(status_code=503, detail=f"AI recommendations disabled: {reason}")
    if not check_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    key = x_api_key or (request.client.host if request.client else "anon")
    if not rate_limit_ok(key):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    if restaurant_id is not None and not tenant_allowed(x_api_key, restaurant_id):
        raise HTTPException(status_code=403, detail="restaurant not allowed for this API key")


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
    is_available = True
    reason = "availability_unconfigured"

    if normalized == "outofstock":
        is_available = False
        reason = "out_of_stock"
    elif normalized == "staticquantity":
        if remaining is not None and remaining <= 0:
            is_available = False
            reason = "quantity_depleted"
        else:
            reason = "quantity_available" if remaining is not None else "quantity_unknown"
    elif normalized == "dependonstock":
        if remaining is not None and remaining <= 0:
            is_available = False
            reason = "stock_depleted"
        else:
            reason = "stock_available" if remaining is not None else "stock_managed_unknown"
    elif normalized == "alwaysavailable":
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
    global _RESTAURANTS_CACHE
    with _LIVE_CACHE_LOCK:
        _RESTAURANTS_CACHE = None
        _MENU_CACHE.clear()


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
    now = time.monotonic()
    if not fresh:
        with _LIVE_CACHE_LOCK:
            cached = _MENU_CACHE.get(key)
            if cached is not None and cached[0] > now:
                return cached[1]
            load_lock = _MENU_LOAD_LOCKS.setdefault(key, Lock())
    else:
        with _LIVE_CACHE_LOCK:
            load_lock = _MENU_LOAD_LOCKS.setdefault(key, Lock())

    with load_lock:
        now = time.monotonic()
        if not fresh:
            with _LIVE_CACHE_LOCK:
                cached = _MENU_CACHE.get(key)
                if cached is not None and cached[0] > now:
                    return cached[1]
        payload = _fetch_live_menu_payload(int(restaurant_id), bool(include_inactive))
        with _LIVE_CACHE_LOCK:
            _MENU_CACHE[key] = (time.monotonic() + MENU_CACHE_TTL_SECONDS, payload)
        return payload


def _live_item_availability_payload(restaurant_id: int, item_id: int):
    frame = fetch_restaurant_item_availability(restaurant_id, item_id)
    if frame.empty:
        raise HTTPException(status_code=404, detail="restaurant not found")
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

    return {
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
        "model_key": "popularity",
        "label_ar": "الأكثر طلبًا",
        "description_ar": "يرتب الأصناف الأكثر طلبًا في المطعم.",
        "score_label_ar": "قوة الطلب",
        "business_type": "popular",
        "score_scale": 0.25,
    },
}

_ENSEMBLE_META = {
    "model_key": "ensemble",
    "label_ar": "المزيج الذكي",
    "description_ar": "يرتب نتائج السلة والارتباط والتشابه مع أولوية لإجماع المحركات.",
}

_CART_MODEL_CONTEXT_ORDER = [
    "based_on_cart",
    "based_on_last_item",
    "similar_alternatives",
]

_ENSEMBLE_CONTEXT_WEIGHTS = {
    "based_on_cart": 1.00,
    "based_on_last_item": 0.82,
    "similar_alternatives": 0.62,
}

_MODEL_ALLOWED_SOURCES = {
    "based_on_cart": {"restaurant_fbt", "pooled_fbt"},
    "based_on_last_item": {"restaurant_fbt", "pooled_fbt"},
    "similar_alternatives": {"item2vec"},
    "popular": {"restaurant_popularity", "global_common"},
}


def _recommendation_context(section_name: str, item: dict) -> str:
    # Section identity represents the strategy that generated the candidate.
    # ``source`` is supporting evidence and must not move an item to another model.
    if section_name in _CONTEXT_META:
        return section_name
    source = str(item.get("source") or "").lower()
    recommendation_type = str(item.get("recommendation_type") or "").lower()
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
    if context == "popular":
        # Popularity is meaningful relative to the restaurant's own catalog.
        # Present it as demand strength, not as a raw purchase probability.
        calibrated = 0.58 + (0.34 * math.sqrt(relative_signal)) + (0.05 * rank_signal)
        return float(round(max(1.0, min(calibrated * 100.0, 97.0))))
    calibrated = base_signal + (1.0 - base_signal) * (
        0.08 + 0.10 * relative_signal + 0.04 * rank_signal
    )
    return float(round(max(1.0, min(calibrated * 100.0, 97.0))))


def _balanced_model_results(pools: dict, contexts: list[str], limit: int) -> list[dict]:
    """Blend cart models with full-cart priority and an agreement bonus."""
    candidates = {}
    priority_index = {context: index for index, context in enumerate(contexts)}
    for context in contexts:
        items = pools.get(context) or []
        context_weight = _ENSEMBLE_CONTEXT_WEIGHTS.get(context, 0.5)
        item_count = max(1, len(items))
        for item in items:
            item_id = int(item["item_id"])
            rank = max(1, int(item.get("rank") or 1))
            compatibility = float(item.get("compatibility_percent") or 0.0) / 100.0
            rank_signal = max(0.0, 1.0 - ((rank - 1) / item_count))
            weighted_signal = min(
                0.97,
                context_weight * ((0.85 * compatibility) + (0.15 * rank_signal)),
            )
            current = candidates.get(item_id)
            if current is None:
                candidates[item_id] = {
                    "representative": item,
                    "miss_probability": 1.0 - weighted_signal,
                    "contexts": {context},
                    "has_strong_signal": bool(item.get("meets_threshold")),
                }
                continue
            current["miss_probability"] *= 1.0 - weighted_signal
            current["contexts"].add(context)
            current["has_strong_signal"] = (
                current["has_strong_signal"] or bool(item.get("meets_threshold"))
            )
            current_context = current["representative"].get("recommendation_context")
            if priority_index.get(context, 99) < priority_index.get(current_context, 99):
                current["representative"] = item

    ranked = []
    for item_id, candidate in candidates.items():
        contexts_count = len(candidate["contexts"])
        combined_score = 1.0 - candidate["miss_probability"]
        if candidate["has_strong_signal"]:
            compatibility_percent = round(70.0 + (27.0 * combined_score))
        else:
            compatibility_percent = min(69, round(combined_score * 100.0))
        compatibility_percent = max(1, min(97, compatibility_percent))
        meets_threshold = compatibility_percent >= 70
        representative = {
            **candidate["representative"],
            "model_agreement_count": contexts_count,
            "compatibility_percent": float(compatibility_percent),
            "confidence_band_ar": _confidence_band_ar(compatibility_percent),
            "meets_threshold": meets_threshold,
        }
        ranked.append((
            combined_score,
            priority_index.get(representative.get("recommendation_context"), len(contexts)),
            int(representative.get("rank") or 1),
            item_id,
            representative,
        ))
    ranked.sort(key=lambda row: (-row[0], row[1], row[2], row[3]))
    return [row[4] for row in ranked[:limit]]


def _apply_live_recommendation_rules(
    result: dict,
    live_menu: dict,
    cart_item_ids,
    last_added_item_id=None,
    previous_top_item_id=None,
    display_limit: int = 5,
):
    live_items = {int(item["item_id"]): item for item in live_menu["items"]}
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
        "model_source_mismatch": 0,
    }

    for section_name, section_items in (result.get("sections") or {}).items():
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
            if has_cart and context == "popular":
                skipped["popular_after_cart"] += 1
                continue
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
            if item_id in pool_seen[context]:
                continue
            pool_seen[context].add(item_id)
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

    if not has_cart:
        for context in ("based_on_cart", "based_on_last_item", "similar_alternatives"):
            pools[context] = []
    else:
        pools["popular"] = []

    for context, items in pools.items():
        items.sort(key=lambda item: (-float(item.get("score") or 0.0), int(item["item_id"])))
        safe_scores = [max(0.0, float(item.get("score") or 0.0)) for item in items]
        max_score = max(safe_scores, default=0.0)
        for rank, (item, raw_score) in enumerate(zip(items, safe_scores), start=1):
            compatibility = _calibrated_match_score(context, raw_score, max_score, rank - 1)
            item["rank"] = rank
            item["compatibility_percent"] = compatibility
            item["meets_threshold"] = compatibility >= 70.0
            item["confidence_band_ar"] = _confidence_band_ar(compatibility)
            # The packaged artifact has ranking scores but no temporal probability
            # calibrator, so do not present these values as purchase probabilities.
            item["probability_percent"] = None

    priority = _CART_MODEL_CONTEXT_ORDER if has_cart else ["popular"]
    max_results = max(1, min(int(display_limit or 5), 5))
    visible_pools = {}
    strong_pools = {}
    model_fallbacks = {}
    for context, items in pools.items():
        strong = [item for item in items if item["meets_threshold"]]
        strong_pools[context] = strong[:max_results]
        model_fallbacks[context] = bool(items and not strong)
        visible_pools[context] = (strong or items)[:max_results]

    agreement_counts = {}
    for context in priority:
        for item in visible_pools[context]:
            item_id = int(item["item_id"])
            agreement_counts[item_id] = agreement_counts.get(item_id, 0) + 1
    for items in pools.values():
        for item in items:
            item["model_agreement_count"] = agreement_counts.get(int(item["item_id"]), 1)

    has_strong_recommendations = any(strong_pools[context] for context in priority)
    if has_strong_recommendations:
        strong_item_ids = {
            int(item["item_id"])
            for context in priority
            for item in strong_pools[context]
        }
        # Keep weaker corroborating signals for an otherwise strong candidate,
        # while preventing weak-only candidates from polluting the ensemble.
        ensemble_pools = {
            context: [
                item
                for item in visible_pools[context]
                if int(item["item_id"]) in strong_item_ids
            ]
            for context in priority
        }
    else:
        ensemble_pools = visible_pools
    top_recommendations = (
        _balanced_model_results(ensemble_pools, priority, max_results)
        if has_cart
        else list(ensemble_pools["popular"][:max_results])
    )
    threshold_fallback_used = bool(top_recommendations and not has_strong_recommendations)

    if has_cart:
        models = [{
            **_ENSEMBLE_META,
            "available": bool(top_recommendations),
            "description_ar": _ENSEMBLE_META["description_ar"],
            "threshold_fallback_used": threshold_fallback_used,
            "suggestions": top_recommendations,
        }]
        for context in _CART_MODEL_CONTEXT_ORDER:
            meta = _CONTEXT_META[context]
            suggestions = visible_pools[context]
            models.append({
                "model_key": meta["model_key"],
                "label_ar": meta["label_ar"],
                "description_ar": meta["description_ar"],
                "available": bool(suggestions),
                "threshold_fallback_used": model_fallbacks[context],
                "suggestions": suggestions,
            })
        default_model_key = "ensemble"
    else:
        popular_meta = _CONTEXT_META["popular"]
        models = [{
            "model_key": popular_meta["model_key"],
            "label_ar": popular_meta["label_ar"],
            "description_ar": popular_meta["description_ar"],
            "available": bool(top_recommendations),
            "threshold_fallback_used": threshold_fallback_used,
            "suggestions": top_recommendations,
        }]
        default_model_key = "popularity"

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
    result["available_model_keys"] = [
        model["model_key"] for model in models if model["available"]
    ]
    result["warnings"] = warnings
    result["threshold_percent"] = 70.0
    result["threshold_fallback_used"] = threshold_fallback_used
    result["fallback_used"] = bool(
        result.get("fallback_used")
        or threshold_fallback_used
        or not top_recommendations
    )
    return result


def _build_widget_recommendations(req: WidgetRecommendationRequest, request: Request):
    t0 = time.perf_counter()
    result = load_model().recommend(
        restaurant_id=req.restaurant_id,
        cart_item_ids=list(req.cart_item_ids),
        last_added_item_id=req.last_added_item_id,
        limit=req.limit,
        previous_top_item_id=req.previous_top_item_id,
    )
    if result.get("disabled_reason"):
        result["threshold_percent"] = 70.0
        result["threshold_fallback_used"] = False
    else:
        # Reuse the short-lived menu snapshot for fast cart interactions. A fresh,
        # item-level availability check remains mandatory when a suggestion is
        # accepted, so stale stock can never be added from the widget.
        live_menu = _live_menu_payload(
            req.restaurant_id,
            include_inactive=False,
            fresh=False,
        )
        result = _apply_live_recommendation_rules(
            result,
            live_menu,
            req.cart_item_ids,
            last_added_item_id=req.last_added_item_id,
            previous_top_item_id=req.previous_top_item_id,
            display_limit=req.limit,
        )
    latency_ms = round(1000 * (time.perf_counter() - t0), 2)
    result["request_id"] = request.state.request_id
    result["latency_ms"] = latency_ms
    return result


@app.get("/health", response_model=HealthResponse)
def health():
    model = load_model()
    eng = model.engine
    disabled, reason = kill_switch_status()
    recall = (model.metadata.get("eval_recall@5") or {}).get("hybrid_production")
    accuracy_percent = None
    if recall is not None:
        accuracy_percent = round(max(0.0, min(float(recall), 1.0)) * 100, 2)
    return HealthResponse(
        status="ok",
        model_version=model.model_version,
        last_trained_at=_META.get("generated_at"),
        model_accuracy_metric="eval_recall@5.hybrid_production",
        model_accuracy_percent=accuracy_percent,
        restaurants_with_fbt=len(eng.restaurants_with_fbt),
        restaurants_with_popularity=len(eng.restaurants_with_pop),
        kill_switch_active=disabled,
        kill_switch_reason=reason or None,
        api_key_required=api_key_required(),
    )


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
    res["request_id"] = request.state.request_id
    if not res.get("recommendations"):
        METRICS.rejected += 1
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
    recs = eng.popular(restaurant_id, top_k=top_k)
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
    res["request_id"] = request.state.request_id
    return res


@app.post("/recommendation-events", response_model=EventAck)
@app.post("/api/v1/recommendation-events", response_model=EventAck)
@app.post("/api/recommendation-events", response_model=EventAck)
def record_event(ev: RecommendationEvent, request: Request, x_api_key: str = Header(default=None)):
    # Keep event logging available during kill switch periods while enforcing auth/rate limits.
    if not check_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    key = x_api_key or (request.client.host if request.client else "anon")
    if not rate_limit_ok(key):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    row = ev.model_dump()
    if not row.get("request_id"):
        row["request_id"] = request.state.request_id
    if not row.get("timestamp"):
        row["timestamp"] = datetime.now(timezone.utc).isoformat()
    row["received_at"] = datetime.now(timezone.utc).isoformat()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = EVENTS_DIR / f"events-{day}.jsonl"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
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
def demo_restaurants():
    """All restaurants from the live DB for the menu trial selector."""
    rows = fetch_restaurants().fillna("").to_dict(orient="records")
    return {"restaurants": rows, "count": len(rows), "source": "database"}


@app.get("/demo/restaurants/{restaurant_id}/live-menu")
def demo_live_restaurant_menu(restaurant_id: int, include_inactive: bool = True):
    """Complete live menu for a restaurant, including items not in model artifacts."""
    restaurants = fetch_restaurants()
    match = restaurants[restaurants["restaurant_id"] == int(restaurant_id)]
    if match.empty:
        raise HTTPException(status_code=404, detail="restaurant not found")
    items = fetch_restaurant_menu(restaurant_id, include_inactive=include_inactive)
    records = items.fillna("").to_dict(orient="records")
    # UI-compatible fields. Signals are filled by the model only when it knows the item.
    for item in records:
        item.update({"has_cross_sell": False, "has_similar_alternatives": False, "popularity_rank": None})
    categories = {}
    for item in records:
        category_id = item.get("category_id")
        key = str(category_id) if category_id != "" else "uncategorized"
        categories[key] = categories.get(key, 0) + 1
    restaurant = match.iloc[0].fillna("").to_dict()
    return {
        "restaurant_id": int(restaurant_id),
        "restaurant_name": restaurant.get("name_ar") or restaurant.get("name") or f"Restaurant {restaurant_id}",
        "items": records,
        "categories": [{"category_id": key, "category": f"الفئة {key}", "count": count} for key, count in categories.items()],
        "default_cart_item_ids": [records[0]["item_id"]] if records else [],
        "source": "database",
        "include_inactive": include_inactive,
    }


@app.get("/api/menu/restaurants")
def menu_restaurants(fresh: bool = False):
    """Live restaurant selector for the Lovable menu project (read-only)."""
    return _cached_restaurants_payload(fresh=fresh)


@app.get("/api/menu/restaurants/{restaurant_id}/items")
def menu_items(restaurant_id: int, include_inactive: bool = False, fresh: bool = False):
    """Live menu from the DB, grouped per item with its available sizes/prices."""
    return _live_menu_payload(restaurant_id, include_inactive, fresh=fresh)


@app.get("/api/menu/restaurants/{restaurant_id}/items/{item_id}/availability")
def menu_item_availability(restaurant_id: int, item_id: int):
    """Fresh, lightweight stock check for one item before accepting it."""
    return _live_item_availability_payload(restaurant_id, item_id)

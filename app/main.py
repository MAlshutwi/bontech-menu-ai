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
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse

from .config import MODEL_VERSION, SERVING
from .model_loader import final_model_path, load_model
from .recommender import get_engine
from .db import (fetch_restaurants, fetch_restaurants_with_menu_counts,
                 fetch_restaurant_menu, fetch_restaurant_menu_with_sizes)
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


def _build_widget_recommendations(req: WidgetRecommendationRequest, request: Request):
    t0 = time.perf_counter()
    result = load_model().recommend(
        restaurant_id=req.restaurant_id,
        cart_item_ids=list(req.cart_item_ids),
        last_added_item_id=req.last_added_item_id,
        limit=req.limit,
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
def menu_restaurants():
    """Live restaurant selector for the Lovable menu project (read-only)."""
    rows = json.loads(fetch_restaurants_with_menu_counts().to_json(orient="records", force_ascii=False))
    return {"restaurants": rows, "count": len(rows), "source": "live_database"}


@app.get("/api/menu/restaurants/{restaurant_id}/items")
def menu_items(restaurant_id: int, include_inactive: bool = False):
    """Live menu from the DB, grouped per item with its available sizes/prices."""
    restaurants = fetch_restaurants()
    match = restaurants[restaurants["restaurant_id"] == int(restaurant_id)]
    if match.empty:
        raise HTTPException(status_code=404, detail="restaurant not found")
    raw = json.loads(fetch_restaurant_menu_with_sizes(restaurant_id, include_inactive).to_json(orient="records", force_ascii=False))
    items_by_id = {}
    for row in raw:
        item_id = int(row["item_id"])
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
            })
    items = list(items_by_id.values())
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
    restaurant = json.loads(match.iloc[0].to_json(force_ascii=False))
    return {
        "restaurant": restaurant,
        "items": items,
        "categories": list(categories.values()),
        "count": len(items),
        "source": "live_database",
        "include_inactive": include_inactive,
    }

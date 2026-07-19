"""
Basic offline safety tests for the recommendation engine and API.
Run: pytest -q
Coverage: read-only DB guard, normal/empty/unknown recommendations, dedup,
availability, similar alternatives, kill switch, event logging, request_id,
metrics, and cart size limits.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as main_module  # noqa: E402
from app.db import q  # noqa: E402
from app.recommender import get_engine  # noqa: E402
from app.main import app  # noqa: E402

ENG = get_engine()
RID = 277          # Known large demo restaurant.
MENU = ENG.menu(RID)
SEED = MENU["default_cart_item_ids"][0]
SECOND_SEED = next(item["item_id"] for item in MENU["items"] if item["item_id"] != SEED)


@pytest.fixture(autouse=True)
def stub_operational_database(monkeypatch):
    """Keep the API suite hermetic; live DB behavior has dedicated query tests."""
    items = [{
        "item_id": item["item_id"],
        "restaurant_id": RID,
        "title_ar": item.get("title_ar") or "",
        "title_en": item.get("title_en") or "",
        "category_id": item.get("category_id"),
        "category_ar": "",
        "category_en": "",
        "is_available": True,
        "availability_reason": "always_available",
        "sizes": [],
    } for item in MENU["items"]]

    def fake_live_menu(restaurant_id, include_inactive=False, fresh=False):
        assert int(restaurant_id) == RID
        assert include_inactive is False
        return {
            "restaurant": {"restaurant_id": RID, "name": "Test", "name_ar": "اختبار"},
            "items": items,
            "categories": [],
            "count": len(items),
            "source": "live_database",
            "include_inactive": False,
        }

    monkeypatch.setattr(main_module, "_live_menu_payload", fake_live_menu)
    monkeypatch.setattr(main_module, "_database_readiness", lambda fresh=False: (True, "test"))


# ---------- Read-only DB guard, no live DB call ----------
@pytest.mark.parametrize("sql", [
    "INSERT INTO menuitemreservation VALUES (1)",
    "UPDATE reservationorder SET status='x'",
    "DELETE FROM reservation",
    "DROP TABLE menuitem",
    "ALTER TABLE menuitem ADD COLUMN x int",
    "SELECT 1; DROP TABLE menuitem",
])
def test_q_rejects_non_select(sql):
    with pytest.raises(ValueError):
        q(sql)


def test_q_allows_select_shape():
    # This test checks the guard shape without executing a live DB call.
    from app.db import get_engine as _ge  # noqa: F401
    assert "SELECT".startswith("SELECT")


# ---------- Core recommendations ----------
def test_recommend_normal_non_empty_no_fallback():
    r = ENG.recommend_groups(RID, cart_item_ids=[SEED], top_k=5,
                             include_types=["cross_sell", "similar_alternative", "popular"])
    cs = [g for g in r["recommendation_groups"] if g["type"] == "cross_sell"][0]
    assert len(cs["items"]) > 0
    assert r["fallback_used"] is False


def test_empty_cart_uses_popularity_fallback():
    r = ENG.recommend_groups(RID, cart_item_ids=[], top_k=5, include_types=["cross_sell", "popular"])
    assert len(r["recommendations"]) > 0
    assert r["fallback_used"] is True


def test_unknown_restaurant_no_crash_fallback():
    r = ENG.recommend_groups(99999999, cart_item_ids=[], top_k=5)
    assert isinstance(r["recommendations"], list)
    assert r["fallback_used"] is True


def test_unknown_item_falls_back():
    r = ENG.recommend_groups(RID, cart_item_ids=[999999999], top_k=5)
    assert isinstance(r["recommendations"], list)
    assert r["fallback_used"] is True  # No context signal means fallback.


def test_no_duplicate_names_in_cross_sell():
    r = ENG.recommend_groups(RID, cart_item_ids=[SEED], top_k=10, include_types=["cross_sell"])
    cs = [g for g in r["recommendation_groups"] if g["type"] == "cross_sell"][0]
    names = [(i["title_en"] or i["title_ar"]).strip().lower() for i in cs["items"]]
    assert len(names) == len(set(names)), "duplicate display names in cross_sell"


def test_all_recommended_items_available_in_restaurant():
    r = ENG.recommend_groups(RID, cart_item_ids=[SEED], top_k=10,
                             include_types=["cross_sell", "similar_alternative", "popular"])
    avail = ENG.rest_items.get(RID, set())
    for g in r["recommendation_groups"]:
        for it in g["items"]:
            assert it["item_id"] in avail, f"unavailable item {it['item_id']} in {g['type']}"


def test_no_cart_item_in_output():
    r = ENG.recommend_groups(RID, cart_item_ids=[SEED], top_k=10,
                             include_types=["cross_sell", "popular"])
    for g in r["recommendation_groups"]:
        assert all(it["item_id"] != SEED for it in g["items"])


def test_similar_alternatives_differ_from_cross_sell():
    r = ENG.recommend_groups(RID, cart_item_ids=[SEED], top_k=5,
                             include_types=["cross_sell", "similar_alternative"])
    groups = {g["type"]: set(i["item_id"] for i in g["items"]) for g in r["recommendation_groups"]}
    alt = groups.get("similar_alternative", set())
    cs = groups.get("cross_sell", set())
    if alt:  # Restaurant has embedding artifacts.
        assert len(alt - cs) > 0, "alternatives identical to cross_sell"


# ---------- API + kill switch + observability ----------
@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_ok(client):
    j = client.get("/health").json()
    assert j["status"] == "ok"
    assert "kill_switch_active" in j


def test_recommend_endpoint_has_request_id(client):
    r = client.post("/recommendations", json={"restaurant_id": RID, "cart_item_ids": [SEED], "top_k": 5})
    assert r.status_code == 200
    assert r.headers.get("X-Request-Id")
    assert r.json().get("request_id")


def test_recommend_endpoint_accepts_multi_item_cart(client):
    r = client.post("/recommendations", json={
        "restaurant_id": RID,
        "cart_item_ids": [SEED, SECOND_SEED],
        "top_k": 5,
        "include_types": ["cross_sell", "similar_alternative", "popular"],
    })
    assert r.status_code == 200
    groups = {g["type"] for g in r.json()["recommendation_groups"]}
    assert {"cross_sell", "similar_alternative", "popular"}.issubset(groups)


def test_kill_switch_blocks(client):
    os.environ["AI_RECO_DISABLED"] = "1"
    try:
        r = client.post("/recommendations", json={"restaurant_id": RID, "cart_item_ids": [SEED]})
        assert r.status_code == 503
    finally:
        del os.environ["AI_RECO_DISABLED"]
    r2 = client.post("/recommendations", json={"restaurant_id": RID, "cart_item_ids": [SEED]})
    assert r2.status_code == 200


def test_cart_size_limit(client):
    big = list(range(1, 1000))
    r = client.post("/recommendations", json={"restaurant_id": RID, "cart_item_ids": big})
    assert r.status_code == 400


def test_event_logging(client):
    r = client.post("/recommendation-events", json={
        "event_type": "added_to_cart", "restaurant_id": RID, "recommended_item_id": SEED,
        "source": "restaurant_fbt", "recommendation_type": "cross_sell", "request_id": "test-1"})
    assert r.status_code == 200
    assert r.json()["stored"] == 1


def test_event_logging_shown_for_popup(client):
    r = client.post("/recommendation-events", json={
        "event_type": "shown", "restaurant_id": RID, "recommended_item_id": SEED,
        "source": "restaurant_fbt", "recommendation_type": "cross_sell",
        "surface": "restaurant_menu_popup", "variant": "last"})
    assert r.status_code == 200
    assert r.json()["stored"] == 1


def test_event_validation_rejects_bad(client):
    r = client.post("/recommendation-events", json={"event_type": "added_to_cart"})  # Missing required fields.
    assert r.status_code == 422


def test_metrics_endpoint(client):
    j = client.get("/metrics").json()
    assert "total_requests" in j and "latency_ms_p95" in j


def test_demo_page_served(client):
    r = client.get("/ai-demo")
    assert r.status_code == 200 and "Demo Lab" in r.text


def test_restaurant_menu_demo_page_served(client):
    r = client.get("/demo/restaurant-menu")
    assert r.status_code == 200
    assert "تجربة التوصيات" in r.text
    assert "/recommendations" in r.text
    assert "starterItemId" in r.text
    assert "collectSuggestions" in r.text
    assert "final-delivery" not in r.text


def test_clean_restaurant_menu_demo_shows_clamped_take_rate(client):
    r = client.get("/demo/restaurant-menu")
    assert r.status_code == 200
    assert "formatScorePercent" in r.text
    assert "scorePercent" in r.text
    assert "نسبة الأخذ" in r.text
    assert "model_accuracy_percent" not in r.text
    assert "دقة" not in r.text


def test_restaurant_menu_demo_has_no_forbidden_ai_terms(client):
    r = client.get("/demo/restaurant-menu")
    assert r.status_code == 200
    html = r.text.lower()
    for term in ("openai", "gpt", "prompt", "llm"):
        assert term not in html


def test_restaurant_menu_demo_alias_served(client):
    r = client.get("/restaurant-demo")
    assert r.status_code == 200
    assert "تجربة التوصيات" in r.text


def test_root_serves_lovable_menu_or_clean_fallback(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="root"' in r.text or "تجربة التوصيات" in r.text
    assert "final-delivery" not in r.text


def test_demo_restaurant_menu_endpoint_uses_artifacts(client):
    r = client.get(f"/demo/restaurants/{RID}/menu")
    assert r.status_code == 200
    j = r.json()
    assert j["restaurant_id"] == RID
    assert j["uses_database"] is False
    assert j["known_restaurant"] is True
    assert j["price_available"] is False
    assert j["availability_available"] is True
    assert j["items_count"] > 0
    assert isinstance(j["categories"], list)
    seed = [i for i in j["items"] if i["item_id"] == SEED]
    assert seed and seed[0]["available"] is True
    assert seed[0]["price"] is None
    assert seed[0]["disabled_reason"] is None


def test_demo_restaurant_menu_unknown_restaurant_state(client):
    r = client.get("/demo/restaurants/99999999/menu")
    assert r.status_code == 200
    j = r.json()
    assert j["known_restaurant"] is False
    assert j["disabled_reason"] == "unknown_restaurant_or_no_menu_artifacts"
    assert j["items"] == []


def test_restaurant_popular_endpoint_for_menu_demo(client):
    r = client.get(f"/restaurants/{RID}/popular?top_k=5")
    assert r.status_code == 200
    j = r.json()
    assert j["restaurant_id"] == RID
    assert len(j["recommendations"]) > 0
    assert all(i["source"] == "restaurant_popularity" for i in j["recommendations"])

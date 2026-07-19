import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as main_module  # noqa: E402
from app.main import app  # noqa: E402
from app.recommender import get_engine  # noqa: E402


RID = 277
SEED = get_engine().menu(RID)["default_cart_item_ids"][0]
ROOT = Path(__file__).resolve().parent.parent
LIVE_ONLY_RID = 99_999_999
LIVE_ONLY_ITEMS = [
    {"item_id": 91_000_001, "title_ar": "صنف أساسي", "title_en": "Base item", "category_id": 10},
    {"item_id": 91_000_002, "title_ar": "إضافة مكملة", "title_en": "Complement", "category_id": 20},
    {"item_id": 91_000_003, "title_ar": "خيار آخر", "title_en": "Another option", "category_id": 30},
]


def _live_item(raw):
    return {
        **raw,
        "restaurant_id": LIVE_ONLY_RID,
        "category_ar": "",
        "category_en": "",
        "is_available": True,
        "availability_reason": "always_available",
        "sizes": [],
    }


@pytest.fixture(autouse=True)
def stub_live_database(monkeypatch):
    model_menu = get_engine().menu(RID)
    model_items = [
        _live_item({
            "item_id": item["item_id"],
            "title_ar": item.get("title_ar") or "",
            "title_en": item.get("title_en") or "",
            "category_id": item.get("category_id"),
        })
        for item in model_menu["items"]
    ]
    for item in model_items:
        item["restaurant_id"] = RID

    def fake_live_menu(restaurant_id, include_inactive=False, fresh=False):
        if include_inactive:
            raise AssertionError("tests must not request inactive menu data")
        if int(restaurant_id) == RID:
            items = model_items
        elif int(restaurant_id) == LIVE_ONLY_RID:
            items = [_live_item(item) for item in LIVE_ONLY_ITEMS]
        else:
            raise HTTPException(status_code=404, detail="restaurant not found")
        return {
            "restaurant": {"restaurant_id": int(restaurant_id), "name": "Test", "name_ar": "اختبار"},
            "items": items,
            "categories": [],
            "count": len(items),
            "source": "live_database",
            "include_inactive": False,
        }

    monkeypatch.setattr(main_module, "_live_menu_payload", fake_live_menu)
    monkeypatch.setattr(main_module, "_database_readiness", lambda fresh=False: (True, "test"))


def client():
    return TestClient(app)


def test_widget_api_validation_rejects_bad_inputs():
    c = client()
    bad_payloads = [
        {},
        {"cart_item_ids": [SEED]},
        {"restaurant_id": -1, "cart_item_ids": [SEED]},
        {"restaurant_id": "277", "cart_item_ids": [SEED]},
        {"restaurant_id": RID},
        {"restaurant_id": RID, "cart_item_ids": [str(SEED)]},
        {"restaurant_id": RID, "cart_item_ids": [None]},
        {"restaurant_id": RID, "cart_item_ids": [-5]},
        {"restaurant_id": RID, "cart_item_ids": [], "previous_top_item_id": 0},
        {"restaurant_id": RID, "cart_item_ids": [], "previous_top_item_id": "123"},
    ]
    for payload in bad_payloads:
        r = c.post("/api/v1/recommendations", json=payload)
        assert r.status_code == 422, payload


def test_widget_api_accepts_empty_cart_and_unknowns():
    c = client()
    empty = c.post("/api/v1/recommendations", json={"restaurant_id": RID, "cart_item_ids": []})
    assert empty.status_code == 200
    empty_body = empty.json()
    assert empty_body["fallback_used"] is True
    assert empty_body["default_model_key"] == "time_aware_popularity"
    assert [model["model_key"] for model in empty_body["models"]] == ["time_aware_popularity"]
    assert empty_body["selected_model"]["validated"] is True
    assert empty_body["selected_model"]["validation_metric"] == "recall@10"

    live_only_rest = c.post("/api/v1/recommendations", json={
        "restaurant_id": LIVE_ONLY_RID,
        "cart_item_ids": [LIVE_ONLY_ITEMS[0]["item_id"]],
        "last_added_item_id": LIVE_ONLY_ITEMS[0]["item_id"],
    })
    assert live_only_rest.status_code == 200
    live_only_body = live_only_rest.json()
    assert live_only_body["disabled_reason"] is None
    assert live_only_body["sections"]["based_on_cart"]
    assert all(item["source"] == "live_menu_fallback" for item in live_only_body["sections"]["based_on_cart"])
    assert live_only_body["sections"]["popular"] == []

    unknown_item = c.post("/api/v1/recommendations", json={
        "restaurant_id": RID,
        "cart_item_ids": [999999999],
        "last_added_item_id": 999999999,
    })
    assert unknown_item.status_code == 200
    assert isinstance(unknown_item.json()["warnings"], list)


def test_widget_api_rejects_too_many_cart_items():
    c = client()
    r = c.post("/api/v1/recommendations", json={
        "restaurant_id": RID,
        "cart_item_ids": list(range(1, 1000)),
    })
    assert r.status_code == 400


def test_widget_api_response_safety():
    c = client()
    r = c.post("/api/v1/recommendations", json={
        "restaurant_id": RID,
        "cart_item_ids": [SEED],
        "last_added_item_id": SEED,
    })
    assert r.status_code == 200
    text = r.text.lower()
    forbidden = [
        "password",
        "secret",
        "database_url",
        "traceback",
        "stack trace",
        ".env",
        "c:\\",
        "/users/",
    ]
    for term in forbidden:
        assert term not in text


def test_widget_api_supports_last_item_and_cart_sections():
    c = client()
    r = c.post("/api/v1/recommendations", json={
        "restaurant_id": RID,
        "cart_item_ids": [9531, 10153],
        "last_added_item_id": 10153,
        "limit": 12,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["disabled_reason"] is None
    assert body["sections"]["based_on_last_item"]
    assert body["sections"]["based_on_cart"]
    assert len(body["top_recommendations"]) <= 5
    assert body["sections"]["based_on_last_item"][0]["evidence"].get("with_item") == 10153
    assert body["default_model_key"] == body["selected_model"]["model_key"]
    assert [model["model_key"] for model in body["models"]] == [body["default_model_key"]]
    assert body["selected_model"]["validated"] is True
    assert body["selected_model"]["validation_metric"] == "recall@5"
    assert body["sections"]["popular"] == []
    assert body["sections"]["time_context"] == []
    assert {item["model_key"] for item in body["top_recommendations"]} == {
        body["default_model_key"]
    }
    assert all(item["contributing_models"] for item in body["top_recommendations"])
    assert all(item["source_labels_ar"] for item in body["top_recommendations"])
    assert all(item["accuracy_validated"] for item in body["top_recommendations"])
    assert all(item["type_label_ar"] for item in body["top_recommendations"])
    assert all(1 <= item["compatibility_percent"] <= 99 for item in body["top_recommendations"])
    assert all(item["probability_percent"] is None for item in body["top_recommendations"])
    assert all(float(item["compatibility_percent"]).is_integer() for item in body["top_recommendations"])


def test_widget_api_method_safety():
    c = client()
    r = c.get("/api/v1/recommendations")
    assert r.status_code == 405


def test_widget_api_kill_switch_blocks_and_recovers():
    c = client()
    kill_path = ROOT / "artifacts" / "KILL_SWITCH"
    kill_path.write_text("test", encoding="utf-8")
    try:
        r = c.post("/api/v1/recommendations", json={"restaurant_id": RID, "cart_item_ids": [SEED]})
        assert r.status_code == 503
    finally:
        kill_path.unlink(missing_ok=True)
    r2 = c.post("/api/v1/recommendations", json={"restaurant_id": RID, "cart_item_ids": [SEED]})
    assert r2.status_code == 200


def test_widget_files_are_safe_and_configurable():
    widget_dir = ROOT / "delivery" / "final_model" / "widget"
    js = (widget_dir / "smart-suggestions-widget.js").read_text(encoding="utf-8")
    css = (widget_dir / "smart-suggestions-widget.css").read_text(encoding="utf-8")
    combined = (js + "\n" + css).lower()
    for term in ("openai", "gpt", "prompt", "llm", "api_key", "password", "database_url", "c:\\", ".env"):
        assert term not in combined
    assert "apiBaseUrl" in js
    assert "restaurantId" in js
    assert "formatScorePercent" in js
    assert "limit: 2" in js
    assert "المصدر:" in js
    assert "sourceLabel" in js
    assert "based_on_last_item" in js
    assert "based_on_cart" in js
    assert "مع آخر صنف" in js
    assert "حسب السلة" in js
    assert "btrw-score" in css
    assert "277" not in js


def test_widget_demo_route_and_assets_served():
    c = client()
    assert c.get("/demo/widget-integration").status_code == 200
    assert c.get("/demo/widget/smart-suggestions-widget.js").status_code == 200
    assert c.get("/demo/widget/smart-suggestions-widget.css").status_code == 200
    assert c.get("/demo/smart-suggestions-widget.js").status_code == 200
    assert c.get("/demo/smart-suggestions-widget.css").status_code == 200


def test_final_delivery_demo_route_serves_full_delivery_page():
    c = client()
    r = c.get("/demo/final-delivery")
    assert r.status_code == 200
    assert "BonTech Restaurant Menu Demo" in r.text
    assert "/demo/widget/smart-suggestions-widget.js" in r.text
    assert "smart-suggestions-widget.js" in r.text
    assert "formatScorePercent" in r.text
    assert 'label for="apiBaseUrl">API' not in r.text
    assert 'label for="restaurantId">restaurant_id' not in r.text
    assert "restaurant_id ${restaurantId}" not in r.text
    assert "limit: 2" in r.text
    assert "المصدر:" in r.text
    assert "based_on_last_item" in r.text
    assert "based_on_cart" in r.text
    assert "مع آخر صنف" in r.text
    assert "حسب السلة" in r.text
    assert ".app{height:auto;min-height:100vh;overflow:visible}" in r.text
    assert ".menu-shell{overflow:visible;min-height:auto}" in r.text
    assert ".menu-grid{overflow:visible;min-height:auto}" in r.text
    assert "SmartSuggestionsWidget script did not load" not in r.text
    assert "نسبة دقة المودل" not in r.text


def test_health_exposes_clamped_model_accuracy():
    c = client()
    r = c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["model_accuracy_metric"] == "eval_recall@5.hybrid_production"
    assert 0 <= body["model_accuracy_percent"] <= 100

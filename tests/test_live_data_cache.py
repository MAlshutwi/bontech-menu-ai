import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.db as db
import app.main as main


@pytest.fixture(autouse=True)
def clear_live_caches():
    main._reset_live_data_caches()
    yield
    main._reset_live_data_caches()


def _menu_frame(item_id=101):
    return pd.DataFrame([{
        "restaurant_id": 7,
        "restaurant_name": "Restaurant Seven",
        "restaurant_name_ar": "المطعم السابع",
        "item_id": item_id,
        "title_ar": "صنف",
        "title_en": "Item",
        "category_id": 9,
        "category_ar": "قسم",
        "category_en": "Category",
        "is_published": True,
        "is_deleted": False,
        "is_combo": False,
        "calories": None,
        "item_size_id": 501 if item_id is not None else None,
        "size_ar": "عادي",
        "size_en": "Regular",
        "size_code": "R",
        "price": 10.0,
        "takeaway_price": 10.0,
        "size_is_deleted": False,
        "availability_mode": "AlwaysAvailable",
        "availability_value": None,
        "current_availability_value": None,
    }])


def test_menu_query_includes_restaurant_metadata_in_same_statement(monkeypatch):
    captured = {}

    def fake_q(sql, **params):
        captured["sql"] = sql
        captured["params"] = params
        return pd.DataFrame()

    monkeypatch.setattr(db, "q", fake_q)
    db.fetch_restaurant_menu_with_sizes(7, include_inactive=False)

    normalized = " ".join(captured["sql"].split())
    assert "FROM restaurants r LEFT JOIN menuitem mi ON mi.restaurantsid = r.id" in normalized
    assert "restaurant_name" in normalized
    assert "WHERE r.id = :restaurant_id" in normalized
    assert captured["params"] == {"restaurant_id": 7}


def test_live_menu_build_uses_combined_result_without_restaurant_lookup(monkeypatch):
    calls = 0

    def fake_fetch(restaurant_id, include_inactive):
        nonlocal calls
        calls += 1
        return _menu_frame()

    monkeypatch.setattr(main, "fetch_restaurant_menu_with_sizes", fake_fetch)
    monkeypatch.setattr(
        main,
        "fetch_restaurants",
        lambda: (_ for _ in ()).throw(AssertionError("second restaurant query is forbidden")),
    )

    payload = main._fetch_live_menu_payload(7)

    assert calls == 1
    assert payload["restaurant"] == {
        "restaurant_id": 7,
        "name": "Restaurant Seven",
        "name_ar": "المطعم السابع",
    }
    assert payload["items"][0]["item_id"] == 101
    assert payload["count"] == 1


def test_live_menu_unknown_restaurant_still_returns_404(monkeypatch):
    monkeypatch.setattr(
        main,
        "fetch_restaurant_menu_with_sizes",
        lambda restaurant_id, include_inactive: pd.DataFrame(),
    )

    with pytest.raises(HTTPException) as exc:
        main._fetch_live_menu_payload(999)

    assert exc.value.status_code == 404


def test_existing_restaurant_with_no_active_items_returns_empty_menu(monkeypatch):
    frame = _menu_frame(item_id=None)
    monkeypatch.setattr(
        main,
        "fetch_restaurant_menu_with_sizes",
        lambda restaurant_id, include_inactive: frame,
    )

    payload = main._fetch_live_menu_payload(7)

    assert payload["restaurant"]["restaurant_id"] == 7
    assert payload["items"] == []
    assert payload["categories"] == []
    assert payload["count"] == 0


def test_menu_cache_reuses_value_and_fresh_bypasses_then_updates(monkeypatch):
    calls = 0

    def fake_fetch(restaurant_id, include_inactive):
        nonlocal calls
        calls += 1
        return {"restaurant": {"restaurant_id": restaurant_id}, "generation": calls}

    monkeypatch.setattr(main, "_fetch_live_menu_payload", fake_fetch)

    first = main._live_menu_payload(7)
    cached = main._live_menu_payload(7)
    refreshed = main._live_menu_payload(7, fresh=True)
    cached_after_refresh = main._live_menu_payload(7)

    assert calls == 2
    assert first is cached
    assert refreshed["generation"] == 2
    assert cached_after_refresh is refreshed


def test_restaurant_cache_reuses_value_and_supports_fresh(monkeypatch):
    calls = 0

    def fake_fetch():
        nonlocal calls
        calls += 1
        return pd.DataFrame([{
            "restaurant_id": calls,
            "name": f"Restaurant {calls}",
            "name_ar": "",
            "total_item_count": 1,
            "active_item_count": 1,
        }])

    monkeypatch.setattr(main, "fetch_restaurants_with_menu_counts", fake_fetch)

    first = main._cached_restaurants_payload()
    cached = main._cached_restaurants_payload()
    refreshed = main._cached_restaurants_payload(fresh=True)

    assert calls == 2
    assert first is cached
    assert refreshed["restaurants"][0]["restaurant_id"] == 2


def test_single_item_availability_payload_uses_fresh_size_stock(monkeypatch):
    frame = pd.DataFrame([
        {
            "restaurant_id": 7,
            "item_id": 101,
            "category_id": 9,
            "item_size_id": 501,
            "size_ar": "صغير",
            "size_en": "Small",
            "size_code": "S",
            "price": 8.0,
            "takeaway_price": 8.0,
            "availability_mode": "OutOfStock",
            "availability_value": None,
            "current_availability_value": 0,
        },
        {
            "restaurant_id": 7,
            "item_id": 101,
            "category_id": 9,
            "item_size_id": 502,
            "size_ar": "كبير",
            "size_en": "Large",
            "size_code": "L",
            "price": 12.0,
            "takeaway_price": 12.0,
            "availability_mode": "StaticQuantity",
            "availability_value": None,
            "current_availability_value": 3,
        },
    ])
    monkeypatch.setattr(
        main,
        "fetch_restaurant_item_availability",
        lambda restaurant_id, item_id: frame,
    )

    payload = main._live_item_availability_payload(7, 101)

    assert payload["is_available"] is True
    assert payload["available_size_count"] == 1
    assert payload["sizes"][0]["is_available"] is False
    assert payload["sizes"][1]["is_available"] is True


def test_restaurant_endpoint_accepts_fresh_and_large_json_is_gzipped(monkeypatch):
    seen = []
    large_payload = {
        "restaurants": [
            {
                "restaurant_id": index,
                "name": "Restaurant " + ("x" * 80),
                "name_ar": "مطعم",
                "total_item_count": 10,
                "active_item_count": 10,
            }
            for index in range(30)
        ],
        "count": 30,
        "source": "live_database",
    }

    def fake_payload(fresh=False):
        seen.append(fresh)
        return large_payload

    monkeypatch.setattr(main, "_cached_restaurants_payload", fake_payload)
    with TestClient(main.app) as client:
        response = client.get(
            "/api/menu/restaurants?fresh=true",
            headers={"Accept-Encoding": "gzip"},
        )

    assert response.status_code == 200
    assert seen == [True]
    assert response.headers.get("content-encoding") == "gzip"

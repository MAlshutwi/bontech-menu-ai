import pandas as pd

from app.trend import build_current_trend_nowcast


AS_OF = pd.Timestamp("2026-07-19T12:00:00Z")


def _counts(rows, *, latest="2026-07-19T11:00:00Z", as_of=AS_OF):
    payload = []
    for row in rows:
        payload.append({
            **row,
            "latest_order_at": latest,
            "data_as_of": as_of,
        })
    if not payload:
        payload.append({
            "item_id": None,
            "time_period_key": None,
            "recent_orders": None,
            "baseline_orders": None,
            "latest_order_at": latest,
            "data_as_of": as_of,
        })
    return pd.DataFrame(payload)


def test_nowcast_ranks_observed_growth_without_fake_probability_or_accuracy():
    frame = _counts([
        {"item_id": 1, "time_period_key": "afternoon", "recent_orders": 10, "baseline_orders": 8},
        {"item_id": 2, "time_period_key": "afternoon", "recent_orders": 4, "baseline_orders": 0},
        {"item_id": 3, "time_period_key": "afternoon", "recent_orders": 2, "baseline_orders": 0},
    ])

    payload = build_current_trend_nowcast(
        frame,
        period_key="afternoon",
        period_ar="العصر",
        titles={1: ("One", "واحد"), 2: ("Two", "اثنان"), 3: ("Three", "ثلاثة")},
    )

    assert payload["status"] == "available"
    assert payload["scope"] == "same_time_period"
    assert payload["is_forecast"] is False
    assert [item["item_id"] for item in payload["items"]] == [1, 2]
    first = payload["items"][0]
    assert first["evidence"]["observation_type"] == "current_nowcast_not_forecast"
    assert first["evidence"]["score_is_probability"] is False
    assert first["evidence"]["recent_window_days"] == 7
    assert first["evidence"]["baseline_window_days"] == 28
    assert first["evidence"]["observed_growth_percent"] == 400.0


def test_nowcast_deduplicates_common_items_before_serving():
    frame = _counts([
        {"item_id": 1, "time_period_key": "lunch", "recent_orders": 10, "baseline_orders": 8},
        {"item_id": 2, "time_period_key": "lunch", "recent_orders": 8, "baseline_orders": 8},
        {"item_id": 3, "time_period_key": "lunch", "recent_orders": 7, "baseline_orders": 8},
    ])

    payload = build_current_trend_nowcast(
        frame,
        period_key="lunch",
        period_ar="الظهر",
        item_groups={1: 100, 2: 100, 3: 200},
    )

    assert [item["item_id"] for item in payload["items"]] == [1, 3]


def test_nowcast_uses_explicit_all_day_fallback_only_with_fresh_data():
    frame = _counts([
        {"item_id": 1, "time_period_key": "breakfast", "recent_orders": 5, "baseline_orders": 4},
        {"item_id": 1, "time_period_key": "lunch", "recent_orders": 5, "baseline_orders": 4},
    ])

    payload = build_current_trend_nowcast(
        frame,
        period_key="dinner",
        period_ar="الليل",
    )

    assert payload["status"] == "fallback"
    assert payload["scope"] == "restaurant_all_day"
    assert payload["unavailable_reason"] == "insufficient_same_period_observations"
    assert payload["items"]


def test_stale_history_never_returns_old_items_as_current_trend():
    frame = _counts(
        [{"item_id": 1, "time_period_key": "afternoon", "recent_orders": 20, "baseline_orders": 1}],
        latest="2025-10-28T10:00:00Z",
    )

    payload = build_current_trend_nowcast(
        frame,
        period_key="afternoon",
        period_ar="العصر",
        max_freshness_hours=48,
    )

    assert payload["status"] == "unavailable"
    assert payload["unavailable_reason"] == "stale_order_history"
    assert payload["freshness_status"] == "stale"
    assert payload["latest_order_at"].startswith("2025-10-28")
    assert payload["items"] == []


def test_fresh_data_without_supported_growth_is_honestly_unavailable():
    frame = _counts([
        {"item_id": 1, "time_period_key": "afternoon", "recent_orders": 2, "baseline_orders": 40},
    ])

    payload = build_current_trend_nowcast(
        frame,
        period_key="afternoon",
        period_ar="العصر",
    )

    assert payload["status"] == "unavailable"
    assert payload["unavailable_reason"] == "no_observed_growth_above_minimum_support"
    assert payload["freshness_status"] == "fresh"
    assert payload["items"] == []

"""Observed current-trend nowcast helpers.

The module describes recent order acceleration against an earlier window. It
does not forecast future demand and never returns a probability or an accuracy
claim for the descriptive momentum score.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd


RECENT_WINDOW_DAYS = 7
BASELINE_WINDOW_DAYS = 28
MIN_RECENT_ORDERS = 3
DEFAULT_MAX_FRESHNESS_HOURS = 48.0
TREND_MODEL_KEY = "current_trend_momentum"


def _as_utc_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("Asia/Riyadh")
    return parsed.tz_convert("UTC")


def _iso(value: pd.Timestamp | None) -> str | None:
    return value.isoformat() if value is not None else None


def _empty_payload(
    *,
    reason: str,
    why_ar: str,
    data_as_of: pd.Timestamp | None,
    latest_order_at: pd.Timestamp | None,
    freshness_status: str,
) -> dict[str, Any]:
    return {
        "model_key": TREND_MODEL_KEY,
        "status": "unavailable",
        "why_ar": why_ar,
        "unavailable_reason": reason,
        "items": [],
        "data_as_of": _iso(data_as_of),
        "latest_order_at": _iso(latest_order_at),
        "freshness_status": freshness_status,
        "scope": None,
        "is_forecast": False,
    }


def _rank_candidates(
    frame: pd.DataFrame,
    *,
    scope: str,
    period_key: str | None,
    period_ar: str | None,
    titles: dict[int, tuple[str, str]],
    item_categories: dict[int, int],
    item_groups: dict[int, int],
    data_as_of: pd.Timestamp,
    latest_order_at: pd.Timestamp,
    limit: int,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        item_id = int(row.item_id)
        recent_orders = max(0, int(row.recent_orders))
        baseline_orders = max(0, int(row.baseline_orders))
        expected_recent = baseline_orders * (RECENT_WINDOW_DAYS / BASELINE_WINDOW_DAYS)
        observed_excess = recent_orders - expected_recent
        if recent_orders < MIN_RECENT_ORDERS or observed_excess <= 0:
            continue
        momentum_score = observed_excess / math.sqrt(expected_recent + 1.0)
        if baseline_orders:
            recent_rate = recent_orders / RECENT_WINDOW_DAYS
            baseline_rate = baseline_orders / BASELINE_WINDOW_DAYS
            observed_growth = round(((recent_rate / baseline_rate) - 1.0) * 100.0, 2)
            trend_status = "rising_observed"
        else:
            recent_rate = recent_orders / RECENT_WINDOW_DAYS
            baseline_rate = 0.0
            observed_growth = None
            trend_status = "emerging_observed"

        title_en, title_ar = titles.get(item_id, ("", ""))
        scope_ar = (
            f"ضمن فترة {period_ar}" if scope == "same_time_period" and period_ar else "على مستوى اليوم كاملًا"
        )
        comparison_ar = (
            f"ارتفع الطلب المرصود {observed_growth:.2f}%"
            if observed_growth is not None
            else "ظهر طلب مرصود حديثًا دون طلب في نافذة المقارنة"
        )
        ranked.append({
            "item_id": item_id,
            "title_ar": title_ar,
            "title_en": title_en,
            "score": round(momentum_score, 6),
            "source": "current_trend_nowcast",
            "recommendation_type": "current_trend",
            "reason": f"{comparison_ar} {scope_ar}؛ هذا رصد حالي وليس توقعًا مستقبليًا.",
            "evidence": {
                "observation_type": "current_nowcast_not_forecast",
                "trend_status": trend_status,
                "scope": scope,
                "time_period_key": period_key if scope == "same_time_period" else None,
                "time_period_ar": period_ar if scope == "same_time_period" else None,
                "recent_window_days": RECENT_WINDOW_DAYS,
                "baseline_window_days": BASELINE_WINDOW_DAYS,
                "recent_order_count": recent_orders,
                "baseline_order_count": baseline_orders,
                "recent_orders_per_day": round(recent_rate, 4),
                "baseline_orders_per_day": round(baseline_rate, 4),
                "observed_growth_percent": observed_growth,
                "observed_excess_orders": round(observed_excess, 4),
                "momentum_score": round(momentum_score, 6),
                "score_is_probability": False,
                "category_id": item_categories.get(item_id),
                "common_group_id": item_groups.get(item_id),
                "data_as_of": _iso(data_as_of),
                "latest_order_at": _iso(latest_order_at),
            },
            "addable": True,
            "disabled_reason": None,
        })

    ranked.sort(
        key=lambda item: (
            -float(item["evidence"]["momentum_score"]),
            -int(item["evidence"]["recent_order_count"]),
            int(item["item_id"]),
        )
    )

    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, Any]] = set()
    for item in ranked:
        item_id = int(item["item_id"])
        group_id = item_groups.get(item_id)
        title_key = " ".join(
            str(item.get("title_ar") or item.get("title_en") or "").casefold().split()
        )
        identity = (
            ("group", group_id)
            if group_id is not None
            else (("title", title_key) if title_key else ("item", item_id))
        )
        if identity in seen:
            continue
        seen.add(identity)
        deduplicated.append(item)
        if len(deduplicated) >= max(1, int(limit)):
            break
    return deduplicated


def build_current_trend_nowcast(
    counts: pd.DataFrame,
    *,
    period_key: str | None,
    period_ar: str | None,
    titles: dict[int, tuple[str, str]] | None = None,
    item_categories: dict[int, int] | None = None,
    item_groups: dict[int, int] | None = None,
    limit: int = 50,
    max_freshness_hours: float = DEFAULT_MAX_FRESHNESS_HOURS,
) -> dict[str, Any]:
    """Build a fresh descriptive trend rail from live aggregated order counts."""
    titles = dict(titles or {})
    item_categories = dict(item_categories or {})
    item_groups = dict(item_groups or {})
    if counts is None or counts.empty:
        return _empty_payload(
            reason="no_order_history",
            why_ar="لا يوجد تاريخ طلبات يمكن منه رصد ترند حالي.",
            data_as_of=None,
            latest_order_at=None,
            freshness_status="no_data",
        )

    first = counts.iloc[0]
    data_as_of = _as_utc_timestamp(first.get("data_as_of")) or pd.Timestamp.now(tz="UTC")
    latest_order_at = _as_utc_timestamp(first.get("latest_order_at"))
    if latest_order_at is None:
        return _empty_payload(
            reason="no_order_history",
            why_ar="لا يوجد تاريخ طلبات يمكن منه رصد ترند حالي.",
            data_as_of=data_as_of,
            latest_order_at=None,
            freshness_status="no_data",
        )

    age_hours = max(0.0, (data_as_of - latest_order_at).total_seconds() / 3600.0)
    if age_hours > max(0.0, float(max_freshness_hours)):
        latest_label = latest_order_at.tz_convert("Asia/Riyadh").strftime("%Y-%m-%d %H:%M")
        return _empty_payload(
            reason="stale_order_history",
            why_ar=(
                f"لا يمكن وصف ترند حالي لأن آخر طلب مسجل كان {latest_label} بتوقيت الرياض "
                f"({age_hours / 24.0:.1f} يوم مضى)."
            ),
            data_as_of=data_as_of,
            latest_order_at=latest_order_at,
            freshness_status="stale",
        )

    usable = counts.copy()
    usable = usable[usable["item_id"].notna()].copy()
    if usable.empty:
        return _empty_payload(
            reason="no_recent_orders",
            why_ar="لا توجد طلبات حديثة داخل نافذة الرصد الحالية.",
            data_as_of=data_as_of,
            latest_order_at=latest_order_at,
            freshness_status="fresh",
        )
    for column in ("recent_orders", "baseline_orders"):
        usable[column] = pd.to_numeric(usable[column], errors="coerce").fillna(0).astype(int)

    period_rows = usable[usable["time_period_key"] == period_key] if period_key else usable.iloc[0:0]
    period_items = _rank_candidates(
        period_rows,
        scope="same_time_period",
        period_key=period_key,
        period_ar=period_ar,
        titles=titles,
        item_categories=item_categories,
        item_groups=item_groups,
        data_as_of=data_as_of,
        latest_order_at=latest_order_at,
        limit=limit,
    )
    if period_items:
        return {
            "model_key": TREND_MODEL_KEY,
            "status": "available",
            "why_ar": (
                f"رصد وصفي لارتفاع الطلب خلال آخر {RECENT_WINDOW_DAYS} أيام مقارنة بالـ"
                f"{BASELINE_WINDOW_DAYS} يومًا السابقة في فترة {period_ar}."
                if period_ar
                else "رصد وصفي لارتفاع الطلب في الفترة الحالية."
            ),
            "unavailable_reason": None,
            "items": period_items,
            "data_as_of": _iso(data_as_of),
            "latest_order_at": _iso(latest_order_at),
            "freshness_status": "fresh",
            "scope": "same_time_period",
            "is_forecast": False,
        }

    all_day = (
        usable.groupby("item_id", as_index=False)[["recent_orders", "baseline_orders"]]
        .sum()
    )
    all_day_items = _rank_candidates(
        all_day,
        scope="restaurant_all_day",
        period_key=None,
        period_ar=None,
        titles=titles,
        item_categories=item_categories,
        item_groups=item_groups,
        data_as_of=data_as_of,
        latest_order_at=latest_order_at,
        limit=limit,
    )
    if all_day_items:
        return {
            "model_key": TREND_MODEL_KEY,
            "status": "fallback",
            "why_ar": "لا توجد إشارة كافية للفترة الحالية؛ عُرض الرصد الحديث على مستوى اليوم كاملًا.",
            "unavailable_reason": "insufficient_same_period_observations",
            "items": all_day_items,
            "data_as_of": _iso(data_as_of),
            "latest_order_at": _iso(latest_order_at),
            "freshness_status": "fresh",
            "scope": "restaurant_all_day",
            "is_forecast": False,
        }

    return _empty_payload(
        reason="no_observed_growth_above_minimum_support",
        why_ar=(
            f"البيانات حديثة، لكن لا يوجد ارتفاع مرصود يتجاوز حد الدعم الأدنى "
            f"({MIN_RECENT_ORDERS} طلبات حديثة) حاليًا."
        ),
        data_as_of=data_as_of,
        latest_order_at=latest_order_at,
        freshness_status="fresh",
    )

"""
Single-file model object used by the BonTech recommendation API.

The object is built from runtime artifacts by scripts/build_final_model_file.py.
Serving code should load the serialized object and should not read parquet/json
model artifacts during request handling.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _as_serving_version(version: str) -> str:
    if str(version).startswith("hybrid_production"):
        return str(version)
    return f"hybrid_production_{version}"


def _unique_positive_ints(values) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        ivalue = int(value)
        if ivalue < 1 or ivalue in seen:
            continue
        seen.add(ivalue)
        out.append(ivalue)
    return out


class BonTechRecommendationModel:
    """
    Prebuilt recommendation model containing all runtime data needed for serving.
    """

    schema_version = "bontech_recommendation_model_file_v1"

    def __init__(
        self,
        engine,
        metadata: dict[str, Any] | None = None,
        source_artifacts: list[str] | None = None,
        built_at: str | None = None,
    ):
        self.engine = engine
        self.model_version = _as_serving_version(getattr(engine, "model_version", "v0"))
        self.raw_model_version = getattr(engine, "model_version", "v0")
        self.metadata = dict(metadata or {})
        self.source_artifacts = list(source_artifacts or [])
        self.built_at = built_at or datetime.now(timezone.utc).isoformat()

        self.restaurant_menus = engine.rest_items
        self.restaurant_items = engine.rest_items
        self.fbt_data = engine.fbt
        self.popularity_data = engine.pop_rank
        self.popularity_scores = engine.pop_score
        self.similar_alternatives = engine.sim_alt
        self.fallback_data = {
            "global_groups": engine.global_groups,
            "pooled_fbt": engine.pooled,
        }
        self.customer_profiles = engine.profiles
        self.item_titles = engine.titles
        self.item_groups = engine.item_group
        self.item_categories = engine.item_category
        self.recency_data = engine.recency
        self.time_aware_popularity = engine.ta_pop
        self.restaurants_with_fbt = set(engine.restaurants_with_fbt)
        self.restaurants_with_popularity = set(engine.restaurants_with_pop)

    @classmethod
    def from_engine(
        cls,
        engine,
        metadata: dict[str, Any] | None = None,
        source_artifacts: list[str] | None = None,
    ) -> "BonTechRecommendationModel":
        return cls(engine=engine, metadata=metadata, source_artifacts=source_artifacts)

    def known_restaurant(self, restaurant_id: int) -> bool:
        rid = int(restaurant_id)
        return bool(
            self.restaurant_items.get(rid)
            or rid in self.restaurants_with_popularity
            or rid in self.restaurants_with_fbt
        )

    def recommend(
        self,
        restaurant_id: int,
        cart_item_ids: list[int],
        last_added_item_id: int | None = None,
        limit: int = 5,
        previous_top_item_id: int | None = None,
    ) -> dict[str, Any]:
        rid = int(restaurant_id)
        top_k = max(1, min(int(limit or 5), 50))
        cart_ids = _unique_positive_ints(cart_item_ids)
        cart_set = set(cart_ids)
        last_item = int(last_added_item_id) if last_added_item_id else None
        previous_top = int(previous_top_item_id) if previous_top_item_id else None
        excluded = {previous_top} if previous_top else set()
        candidate_k = min(50, top_k + (1 if previous_top else 0))
        warnings: list[str] = []
        disabled_reason = None

        sections = {
            "based_on_last_item": [],
            "based_on_cart": [],
            "similar_alternatives": [],
            "popular": [],
        }

        if not self.known_restaurant(rid):
            disabled_reason = "unknown_restaurant_or_no_menu_artifacts"
            warnings.append(disabled_reason)
            return self._response(rid, cart_ids, last_item, sections, [], True, disabled_reason, warnings)

        if last_item and last_item not in cart_set:
            warnings.append("last_added_item_id_not_in_cart_item_ids")

        if not cart_ids:
            popular_items = self.engine.popular(
                rid,
                top_k=candidate_k,
                exclude=list(excluded),
            )
            popular_payload = {
                "recommendation_groups": [{"type": "popular", "items": popular_items}],
                "fallback_used": True,
            }
            sections["popular"], skipped = self._pick_group_items(
                rid,
                popular_payload,
                cart_set,
                set(),
                ["popular"],
                top_k,
                excluded,
            )
            warnings.extend(skipped[:10])
            top_recommendations = self._top_recommendations(sections, top_k, has_cart=False)
            if previous_top and not top_recommendations:
                warnings.append("no_alternative_after_rotation")
            return self._response(
                rid,
                cart_ids,
                last_item,
                sections,
                top_recommendations,
                True,
                disabled_reason,
                warnings,
            )

        include = ["cross_sell", "similar_alternative"]
        context = {
            "source": "pos_widget",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        def call_engine(ids: list[int]) -> dict[str, Any]:
            return self.engine.recommend_groups(
                restaurant_id=rid,
                cart_item_ids=ids,
                top_k=candidate_k,
                include_types=include,
                context=context,
                cart_only=True,
            )

        last_payload = call_engine([last_item]) if last_item else {
            "recommendation_groups": [],
            "fallback_used": True,
        }
        cart_payload = call_engine(cart_ids)
        skipped_all: list[str] = []
        if last_item:
            sections["based_on_last_item"], skipped = self._pick_group_items(
                rid,
                last_payload,
                cart_set,
                set(),
                ["cross_sell"],
                top_k,
                excluded,
            )
            skipped_all.extend(skipped)
        sections["based_on_cart"], skipped = self._pick_group_items(
            rid,
            cart_payload,
            cart_set,
            set(),
            ["cross_sell"],
            top_k,
            excluded,
        )
        skipped_all.extend(skipped)
        sections["similar_alternatives"], skipped = self._pick_group_items(
            rid,
            cart_payload,
            cart_set,
            set(),
            ["similar_alternative"],
            top_k,
            excluded,
        )
        skipped_all.extend(skipped)

        for reason in skipped_all[:10]:
            if reason not in warnings:
                warnings.append(reason)

        top_recommendations = self._top_recommendations(sections, top_k, has_cart=True)
        if previous_top and not top_recommendations:
            warnings.append("no_alternative_after_rotation")
        fallback_used = bool(
            last_payload.get("fallback_used")
            or cart_payload.get("fallback_used")
            or not top_recommendations
            or disabled_reason
        )
        return self._response(
            rid, cart_ids, last_item, sections, top_recommendations, fallback_used, disabled_reason, warnings
        )

    def _response(
        self,
        restaurant_id: int,
        cart_item_ids: list[int],
        last_added_item_id: int | None,
        sections: dict[str, list[dict[str, Any]]],
        top_recommendations: list[dict[str, Any]],
        fallback_used: bool,
        disabled_reason: str | None,
        warnings: list[str],
    ) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "restaurant_id": int(restaurant_id),
            "cart_item_ids": cart_item_ids,
            "last_added_item_id": last_added_item_id,
            "sections": sections,
            "top_recommendations": top_recommendations,
            "fallback_used": bool(fallback_used),
            "disabled_reason": disabled_reason,
            "warnings": warnings,
        }

    def _pick_group_items(
        self,
        restaurant_id: int,
        payload: dict[str, Any],
        cart_set: set[int],
        seen: set[int],
        preferred_types: list[str],
        limit: int,
        excluded_item_ids: set[int] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        groups = {g.get("type"): g for g in payload.get("recommendation_groups", [])}
        out: list[dict[str, Any]] = []
        skipped: list[str] = []
        excluded = excluded_item_ids or set()
        for recommendation_type in preferred_types:
            group = groups.get(recommendation_type)
            if not group:
                continue
            for raw_item in group.get("items", []):
                item_id = int(raw_item.get("item_id"))
                if item_id in excluded:
                    skipped.append(f"previous_top_excluded:{item_id}")
                    continue
                if item_id in seen:
                    skipped.append(f"duplicate_item:{item_id}")
                    continue
                formatted = self._widget_item(restaurant_id, raw_item, recommendation_type, cart_set)
                if not formatted["addable"]:
                    skipped.append(f"{formatted['disabled_reason']}:{item_id}")
                    continue
                out.append(formatted)
                seen.add(item_id)
                if len(out) >= limit:
                    return out, skipped
        return out, skipped

    def _widget_item(
        self,
        restaurant_id: int,
        item: dict[str, Any],
        recommendation_type: str,
        cart_set: set[int],
    ) -> dict[str, Any]:
        item_id = int(item.get("item_id"))
        available = self.restaurant_items.get(int(restaurant_id), set())
        disabled_reason = None
        addable = True
        if item_id in cart_set:
            addable = False
            disabled_reason = "already_in_cart"
        elif item_id not in available:
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

    def _top_recommendations(
        self,
        sections: dict[str, list[dict[str, Any]]],
        limit: int,
        has_cart: bool,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        top_limit = min(2, max(1, int(limit)))
        section_order = (
            ("based_on_cart", "based_on_last_item", "similar_alternatives")
            if has_cart
            else ("popular",)
        )
        for section_name in section_order:
            for item in sections[section_name]:
                item_id = int(item["item_id"])
                if item_id in seen:
                    continue
                seen.add(item_id)
                out.append(item)
                if len(out) >= top_limit:
                    return out
        return out

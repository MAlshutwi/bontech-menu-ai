"""
Single-file model object used by the BonTech recommendation API.

The object is built from runtime artifacts by scripts/build_final_model_file.py.
Serving code should load the serialized object and should not read parquet/json
model artifacts during request handling.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


RIYADH_TIMEZONE = ZoneInfo("Asia/Riyadh")
TIME_PERIOD_LABELS_AR = {
    "breakfast": "الصباح",
    "lunch": "الظهر",
    "afternoon": "العصر",
    "dinner": "الليل",
    "late_night": "آخر الليل",
}
MODEL_LABELS_AR = {
    "fbt_confidence": "الارتباط حسب الثقة",
    "fbt_hybrid": "الارتباط الهجين",
    "fbt_paircount": "الارتباط حسب تكرار الطلب",
    "fbt_lift": "الارتباط حسب قوة الرفع",
    "time_aware_popularity": "الأكثر طلبًا في هذه الفترة",
    "restaurant_popularity": "الأكثر طلبًا",
    "item2vec": "التشابه السلوكي",
    "pooled_fbt": "ارتباط المطاعم المشابهة",
    "live_menu_fallback": "استكشاف المنيو المتاح",
}


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


def _normalized_title(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


class BonTechRecommendationModel:
    """
    Prebuilt recommendation model containing all runtime data needed for serving.
    """

    schema_version = "bontech_recommendation_model_file_v2"

    def __init__(
        self,
        engine,
        metadata: dict[str, Any] | None = None,
        source_artifacts: list[str] | None = None,
        built_at: str | None = None,
    ):
        self.engine = engine
        self.model_version = _as_serving_version(getattr(engine, "model_version", "v0"))
        # Use one canonical version everywhere in the packaged serving object.
        self.engine.model_version = self.model_version
        self.raw_model_version = self.model_version
        self.artifact_schema_version = self.schema_version
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
        # Customer-level profiles are not needed by the restaurant widget and must
        # never be distributed inside the portable model artifact.
        self.engine.profiles = {}
        self.customer_profiles = {}
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
        live_candidates: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rid = int(restaurant_id)
        top_k = max(1, min(int(limit or 5), 50))
        cart_ids = _unique_positive_ints(cart_item_ids)
        cart_set = set(cart_ids)
        last_item = int(last_added_item_id) if last_added_item_id else None
        previous_top = int(previous_top_item_id) if previous_top_item_id else None
        excluded = {previous_top} if previous_top else set()
        configured_pool = int(getattr(self.engine, "candidate_pool_size", 40) or 40)
        candidate_k = min(100, max(top_k * 4, configured_pool, top_k + len(excluded)))
        warnings: list[str] = []
        disabled_reason = None
        live_items = self._normalize_live_candidates(live_candidates)
        request_context = dict(context or {})
        if not request_context.get("timestamp"):
            request_context["timestamp"] = datetime.now(RIYADH_TIMEZONE).isoformat()
        time_period_key = self.engine._bucket(request_context)
        time_period_ar = TIME_PERIOD_LABELS_AR.get(time_period_key)

        sections = {
            "based_on_last_item": [],
            "based_on_cart": [],
            "similar_alternatives": [],
            "popular": [],
            "time_context": [],
        }

        if last_item and last_item not in cart_set:
            warnings.append("last_added_item_id_not_in_cart_item_ids")
            last_item = cart_ids[-1] if cart_ids else None

        if not self.known_restaurant(rid) and not live_items:
            disabled_reason = "unknown_restaurant_or_no_menu_artifacts"
            warnings.append(disabled_reason)
            return self._response(
                rid, cart_ids, last_item, sections, [], True, disabled_reason, warnings,
                selected_model=self._unvalidated_model("live_menu_fallback", "no_menu_candidates"),
                time_period_key=time_period_key,
                time_period_ar=time_period_ar,
            )

        if not cart_ids:
            popular_items = (
                self.engine.popular(rid, top_k=candidate_k, exclude=list(excluded))
                if self.known_restaurant(rid)
                else []
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
                candidate_k,
                excluded,
            )
            time_items = (
                self.engine._time_based_list(rid, set(), request_context, candidate_k)
                if self.known_restaurant(rid)
                else []
            )
            time_payload = {
                "recommendation_groups": [{"type": "time_based", "items": time_items}],
                "fallback_used": not bool(time_items),
            }
            sections["time_context"], time_skipped = self._pick_group_items(
                rid,
                time_payload,
                cart_set,
                set(),
                ["time_based"],
                candidate_k,
                excluded,
            )
            if not sections["popular"] and live_items:
                sections["popular"] = self._live_fallback_items(
                    live_items,
                    cart_ids,
                    last_item,
                    candidate_k,
                    excluded,
                    has_cart=False,
                )
                warnings.append("live_menu_discovery_fallback")
            warnings.extend(skipped[:10])
            warnings.extend(time_skipped[:10])
            if sections["time_context"]:
                selected_context = "time_context"
                selected_model = self._empty_cart_selection(time_period_key, use_time=True)
            elif sections["popular"] and sections["popular"][0].get("source") != "live_menu_fallback":
                selected_context = "popular"
                selected_model = self._empty_cart_selection(time_period_key, use_time=False)
            else:
                selected_context = "popular"
                selected_model = self._unvalidated_model(
                    "live_menu_fallback", "no_validated_popularity_artifact"
                )
            top_recommendations = self._top_recommendations(
                sections, top_k, has_cart=False, selected_context=selected_context
            )
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
                selected_model=selected_model,
                time_period_key=time_period_key,
                time_period_ar=time_period_ar,
            )

        include = ["cross_sell", "similar_alternative", "popular", "time_based"]
        request_context.setdefault("source", "pos_widget")
        selected_model = self._cart_selection(rid)
        ranking_strategy = selected_model.get("strategy") or "fbt_hybrid"

        def call_engine(ids: list[int]) -> dict[str, Any]:
            return self.engine.recommend_groups(
                restaurant_id=rid,
                cart_item_ids=ids,
                top_k=candidate_k,
                include_types=include,
                context=request_context,
                cart_only=True,
                ranking_strategy=ranking_strategy,
            )

        last_payload = call_engine([last_item]) if last_item and self.known_restaurant(rid) else {
            "recommendation_groups": [],
            "fallback_used": True,
        }
        cart_payload = call_engine(cart_ids) if self.known_restaurant(rid) else {
            "recommendation_groups": [],
            "fallback_used": True,
        }
        skipped_all: list[str] = []
        if last_item:
            sections["based_on_last_item"], skipped = self._pick_group_items(
                rid,
                last_payload,
                cart_set,
                set(),
                ["cross_sell"],
                candidate_k,
                excluded,
            )
            skipped_all.extend(skipped)
        sections["based_on_cart"], skipped = self._pick_group_items(
            rid,
            cart_payload,
            cart_set,
            set(),
            ["cross_sell"],
            candidate_k,
            excluded,
        )
        skipped_all.extend(skipped)
        sections["similar_alternatives"], skipped = self._pick_group_items(
            rid,
            cart_payload,
            cart_set,
            set(),
            ["similar_alternative"],
            candidate_k,
            excluded,
        )
        skipped_all.extend(skipped)
        sections["popular"], skipped = self._pick_group_items(
            rid,
            cart_payload,
            cart_set,
            set(),
            ["popular"],
            candidate_k,
            excluded,
        )
        skipped_all.extend(skipped)
        sections["time_context"], skipped = self._pick_group_items(
            rid,
            cart_payload,
            cart_set,
            set(),
            ["time_based"],
            candidate_k,
            excluded,
        )
        skipped_all.extend(skipped)

        supporting_sections = {
            "popular": list(sections["popular"]),
            "time_context": list(sections["time_context"]),
        }
        # Popularity and time are corroborating evidence after the cart starts;
        # they are not standalone cart recommendations.
        sections["popular"] = []
        sections["time_context"] = []

        live_fallback_used = False
        if live_items:
            existing = {int(item["item_id"]) for item in sections["based_on_cart"]}
            reserves = self._live_fallback_items(
                live_items,
                cart_ids,
                last_item,
                candidate_k,
                excluded | existing,
                has_cart=True,
            )
            if reserves:
                sections["based_on_cart"].extend(reserves)
                live_fallback_used = not bool(sections["based_on_cart"][:-len(reserves)])
                if live_fallback_used:
                    warnings.append("live_menu_cart_fallback")

        for reason in skipped_all[:10]:
            if reason not in warnings:
                warnings.append(reason)

        selected_sources = {
            str(item.get("source") or "") for item in sections["based_on_cart"]
        }
        if selected_sources and "restaurant_fbt" not in selected_sources:
            if "pooled_fbt" in selected_sources:
                selected_model = self._unvalidated_model("pooled_fbt", "pooled_fallback_not_validated")
            elif "live_menu_fallback" in selected_sources:
                selected_model = self._unvalidated_model(
                    "live_menu_fallback", "live_menu_fallback_not_validated"
                )
        top_recommendations = self._top_recommendations(
            sections, top_k, has_cart=True, selected_context="based_on_cart"
        )
        if previous_top and not top_recommendations:
            warnings.append("no_alternative_after_rotation")
        fallback_used = bool(
            last_payload.get("fallback_used")
            or cart_payload.get("fallback_used")
            or live_fallback_used
            or not top_recommendations
            or disabled_reason
        )
        return self._response(
            rid,
            cart_ids,
            last_item,
            sections,
            top_recommendations,
            fallback_used,
            disabled_reason,
            warnings,
            selected_model=selected_model,
            time_period_key=time_period_key,
            time_period_ar=time_period_ar,
            supporting_sections=supporting_sections,
        )

    def _validation_catalog(self) -> dict[str, Any]:
        return dict(self.metadata.get("validated_model_selection") or {})

    def _decorate_validation(self, record: dict[str, Any]) -> dict[str, Any]:
        out = dict(record or {})
        model_key = str(out.get("model_key") or out.get("strategy") or "unvalidated")
        out["model_key"] = model_key
        out["label_ar"] = MODEL_LABELS_AR.get(model_key, model_key)
        value = out.get("validation_value")
        out["validation_percent"] = (
            round(max(0.0, min(float(value), 1.0)) * 100.0, 2)
            if value is not None and out.get("validated")
            else None
        )
        out["evaluation_version"] = self._validation_catalog().get("evaluation_version")
        return out

    def _cart_selection(self, restaurant_id: int) -> dict[str, Any]:
        cart = self._validation_catalog().get("cart") or {}
        record = (cart.get("by_restaurant") or {}).get(str(int(restaurant_id)))
        if not record:
            record = cart.get("global") or {}
        if not record:
            return self._unvalidated_model("fbt_hybrid", "validation_metadata_missing")
        return self._decorate_validation(record)

    def _empty_cart_selection(self, time_period_key: str | None, *, use_time: bool) -> dict[str, Any]:
        empty = self._validation_catalog().get("empty_cart") or {}
        key = "time_aware_popularity" if use_time else "restaurant_popularity"
        record = dict(empty.get(key) or {})
        if use_time and time_period_key:
            period_record = (record.get("by_time_period") or {}).get(time_period_key)
            if period_record:
                record.update(period_record)
        if not record:
            return self._unvalidated_model(key, "validation_metadata_missing")
        record["model_key"] = key
        if time_period_key:
            record["time_period_key"] = time_period_key
            record["time_period_ar"] = TIME_PERIOD_LABELS_AR.get(time_period_key)
        return self._decorate_validation(record)

    def _unvalidated_model(self, model_key: str, reason: str) -> dict[str, Any]:
        return self._decorate_validation({
            "model_key": model_key,
            "strategy": model_key,
            "validated": False,
            "validation_metric": None,
            "validation_value": None,
            "validation_trials": 0,
            "validation_scope": "unvalidated",
            "validation_source": None,
            "unavailable_reason": reason,
        })

    def _normalize_live_candidates(
        self,
        candidates: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Validate and deterministically deduplicate an optional live menu snapshot."""
        out: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        seen_titles: set[str] = set()
        for raw in candidates or []:
            try:
                item_id = int(raw.get("item_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            if item_id < 1 or item_id in seen_ids or raw.get("is_available") is False:
                continue
            title_ar = str(raw.get("title_ar") or "").strip()
            title_en = str(raw.get("title_en") or "").strip()
            title_key = _normalized_title(title_ar or title_en)
            if title_key and title_key in seen_titles:
                continue
            category_id = raw.get("category_id")
            try:
                category_id = int(category_id) if category_id is not None else None
            except (TypeError, ValueError):
                category_id = None
            popularity_rank = raw.get("popularity_rank")
            try:
                popularity_rank = max(1, int(popularity_rank)) if popularity_rank is not None else None
            except (TypeError, ValueError):
                popularity_rank = None
            seen_ids.add(item_id)
            if title_key:
                seen_titles.add(title_key)
            out.append({
                "item_id": item_id,
                "title_ar": title_ar,
                "title_en": title_en,
                "category_id": category_id,
                "popularity_rank": popularity_rank,
            })
        return sorted(out, key=lambda item: int(item["item_id"]))

    def _live_fallback_items(
        self,
        candidates: list[dict[str, Any]],
        cart_item_ids: list[int],
        last_item_id: int | None,
        limit: int,
        excluded_item_ids: set[int],
        *,
        has_cart: bool,
    ) -> list[dict[str, Any]]:
        """Build a deterministic menu fallback without presenting it as popularity."""
        cart_set = set(cart_item_ids)
        by_id = {int(item["item_id"]): item for item in candidates}
        last_category = by_id.get(int(last_item_id), {}).get("category_id") if last_item_id else None
        cart_categories = {
            by_id[item_id].get("category_id")
            for item_id in cart_set
            if item_id in by_id and by_id[item_id].get("category_id") is not None
        }

        ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for item in candidates:
            item_id = int(item["item_id"])
            if item_id in cart_set or item_id in excluded_item_ids:
                continue
            category_id = item.get("category_id")
            same_as_last = bool(last_category is not None and category_id == last_category)
            outside_cart_categories = bool(category_id is not None and category_id not in cart_categories)
            popularity_rank = item.get("popularity_rank")
            rank_signal = 1.0 / float(popularity_rank) if popularity_rank else 0.0
            score = 0.18 + (0.08 if not same_as_last else 0.0)
            score += 0.04 if outside_cart_categories else 0.0
            score += min(0.05, 0.05 * rank_signal)
            sort_key = (
                same_as_last,
                not outside_cart_categories,
                popularity_rank if popularity_rank is not None else 1_000_000,
                category_id if category_id is not None else 1_000_000,
                item_id,
            )
            ranked.append((sort_key, {
                "item_id": item_id,
                "title_ar": item.get("title_ar") or "",
                "title_en": item.get("title_en") or "",
                "score": round(score, 4),
                "source": "live_menu_fallback",
                "recommendation_type": "cross_sell" if has_cart else "popular",
                "reason": (
                    "اقتراح تكميلي من المنيو المتاح"
                    if has_cart
                    else "اقتراح استكشافي من المنيو المتاح"
                ),
                "evidence": {
                    "fallback": "live_menu",
                    "category_id": category_id,
                    "avoids_last_item_category": not same_as_last,
                },
                "addable": True,
                "disabled_reason": None,
            }))
        ranked.sort(key=lambda row: row[0])
        return [item for _, item in ranked[: max(1, int(limit))]]

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
        selected_model: dict[str, Any] | None = None,
        time_period_key: str | None = None,
        time_period_ar: str | None = None,
        supporting_sections: dict[str, list[dict[str, Any]]] | None = None,
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
            "selected_model": selected_model,
            "selection_policy": self._validation_catalog().get("selection_policy"),
            "time_period_key": time_period_key,
            "time_period_ar": time_period_ar,
            "supporting_sections": dict(supporting_sections or {}),
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
        selected_context: str | None = None,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        top_limit = max(1, int(limit))
        section_order = (
            (selected_context,)
            if selected_context
            else (
                ("based_on_cart", "based_on_last_item", "similar_alternatives")
                if has_cart
                else ("popular",)
            )
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

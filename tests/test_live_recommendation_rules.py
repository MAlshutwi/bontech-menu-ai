from app.main import _apply_live_recommendation_rules, _availability_state


def _item(item_id, category_id, *, available=True):
    return {
        "item_id": item_id,
        "category_id": category_id,
        "is_available": available,
        "availability_reason": "available_size_exists" if available else "all_sizes_unavailable",
    }


def _rec(item_id, score, source="restaurant_fbt", recommendation_type="cross_sell"):
    return {
        "item_id": item_id,
        "title_ar": f"item {item_id}",
        "title_en": "",
        "score": score,
        "source": source,
        "recommendation_type": recommendation_type,
        "reason": "",
        "evidence": {},
        "addable": True,
        "disabled_reason": None,
    }


def _result(**sections):
    payload = {
        "popular": [],
        "based_on_cart": [],
        "based_on_last_item": [],
        "similar_alternatives": [],
    }
    payload.update(sections)
    return {
        "sections": payload,
        "top_recommendations": [],
        "warnings": [],
        "fallback_used": False,
        "disabled_reason": None,
    }


def test_availability_state_handles_explicit_and_quantity_stock():
    assert _availability_state("OutOfStock", None)["is_available"] is False
    assert _availability_state("StaticQuantity", "0")["availability_reason"] == "quantity_depleted"
    assert _availability_state("DependOnStock", "0.0")["availability_reason"] == "stock_depleted"
    assert _availability_state("DependOnStock", None)["is_available"] is False
    assert _availability_state("DependOnStock", None)["availability_reason"] == "stock_managed_unknown"
    assert _availability_state("StaticQuantity", None)["is_available"] is False
    assert _availability_state("UnknownMode", 10)["is_available"] is False
    assert _availability_state(None, None)["is_available"] is True
    assert _availability_state(None, None)["availability_reason"] == "availability_unconfigured"


def test_empty_cart_starts_with_live_popular_and_drops_out_of_stock():
    live_menu = {"items": [_item(1, 10), _item(2, 11, available=False)]}
    result = _result(
        popular=[
            _rec(1, 0.8, "restaurant_popularity", "popular"),
            _rec(2, 0.9, "restaurant_popularity", "popular"),
            _rec(999, 1.0, "restaurant_popularity", "popular"),
        ],
    )

    ranked = _apply_live_recommendation_rules(result, live_menu, [])

    assert [item["item_id"] for item in ranked["top_recommendations"]] == [1]
    assert ranked["top_recommendations"][0]["recommendation_context"] == "popular"
    assert ranked["top_recommendations"][0]["type_label_ar"] == "الأكثر طلبًا"
    assert ranked["available_model_keys"] == ["restaurant_popularity"]
    assert [model["model_key"] for model in ranked["models"]] == ["restaurant_popularity"]
    assert ranked["default_model_key"] == "restaurant_popularity"
    assert all(
        item["meets_threshold"] == (item["compatibility_percent"] >= 70)
        for item in ranked["top_recommendations"]
    )
    assert "out_of_stock:1" in ranked["warnings"]
    assert "not_live:1" in ranked["warnings"]


def test_cart_priority_and_same_category_filter_prevent_similar_drinks():
    live_menu = {
        "items": [
            _item(10, 5),
            _item(1, 5),
            _item(2, 6),
            _item(3, 7),
        ],
    }
    result = _result(
        popular=[_rec(1, 0.99, "restaurant_popularity", "popular")],
        based_on_cart=[_rec(1, 0.99), _rec(2, 0.20)],
        based_on_last_item=[_rec(3, 0.90)],
    )

    ranked = _apply_live_recommendation_rules(
        result,
        live_menu,
        [10],
        last_added_item_id=10,
    )

    assert [item["item_id"] for item in ranked["top_recommendations"]] == [2]
    full_cart = next(
        model for model in ranked["models"] if model["model_key"] == "full_cart"
    )
    assert full_cart["suggestions"][0]["recommendation_context"] == "based_on_cart"
    assert full_cart["suggestions"][0]["type_label_ar"] == "السلة كاملة"
    assert full_cart["suggestions"][0]["model_key"] == "full_cart"
    assert "same_category_as_last_item:2" in ranked["warnings"]
    assert all(model["model_key"] != "popularity" for model in ranked["models"])
    assert ranked["sections"]["popular"] == []


def test_weak_candidates_use_top_five_fallback_without_fake_70_percent():
    live_menu = {"items": [_item(item_id, item_id + 100) for item_id in range(1, 8)]}
    result = _result(
        based_on_cart=[_rec(item_id, 0.05 - item_id / 1000) for item_id in range(1, 8)],
    )

    ranked = _apply_live_recommendation_rules(result, live_menu, [99])

    assert ranked["threshold_fallback_used"] is True
    assert len(ranked["top_recommendations"]) == 5
    assert all(item["compatibility_percent"] < 70 for item in ranked["top_recommendations"])
    assert ranked["top_recommendations"][0]["probability_percent"] is None


def test_display_limit_is_honored_after_live_ranking():
    live_menu = {"items": [_item(item_id, item_id + 100) for item_id in range(1, 8)]}
    result = _result(
        popular=[
            _rec(item_id, 0.9 - item_id / 100, "restaurant_popularity", "popular")
            for item_id in range(1, 8)
        ],
    )

    ranked = _apply_live_recommendation_rules(result, live_menu, [], display_limit=1)

    assert len(ranked["top_recommendations"]) == 1
    assert ranked["top_recommendations"][0]["item_id"] == 1


def test_selected_cart_model_does_not_blend_alternative_rankings():
    live_menu = {
        "items": [
            _item(10, 5),
            _item(1, 11),
            _item(2, 12),
            _item(3, 13),
            _item(4, 14),
            _item(5, 15),
        ],
    }
    result = _result(
        based_on_cart=[_rec(1, 0.80), _rec(5, 0.50)],
        based_on_last_item=[_rec(2, 0.80)],
        similar_alternatives=[_rec(3, 0.60, "item2vec", "similar_alternative")],
        popular=[_rec(4, 0.50, "restaurant_popularity", "popular")],
    )

    ranked = _apply_live_recommendation_rules(result, live_menu, [10])

    assert ranked["top_recommendations"][0]["model_key"] == "full_cart"
    assert [item["item_id"] for item in ranked["top_recommendations"]] == [1, 5]
    assert len({item["item_id"] for item in ranked["top_recommendations"]}) == 2
    assert all(item["model_key"] != "popularity" for item in ranked["top_recommendations"])
    assert [item["compatibility_percent"] for item in ranked["top_recommendations"]] == sorted(
        [item["compatibility_percent"] for item in ranked["top_recommendations"]],
        reverse=True,
    )


def test_model_catalog_is_independent_and_confidence_is_stable():
    live_menu = {"items": [_item(item_id, item_id + 20) for item_id in range(1, 7)]}
    result = _result(
        based_on_cart=[_rec(1, 0.14), _rec(2, 0.10), _rec(3, 0.05)],
        based_on_last_item=[_rec(1, 0.90), _rec(4, 0.40)],
        similar_alternatives=[_rec(5, 0.60, "item2vec", "similar_alternative")],
        popular=[_rec(6, 0.40, "restaurant_popularity", "popular")],
    )

    ranked = _apply_live_recommendation_rules(result, live_menu, [99])
    groups = {group["model_key"]: group for group in ranked["models"]}

    assert list(groups) == ["full_cart"]
    assert groups["full_cart"]["suggestions"][0]["item_id"] == 1
    assert groups["full_cart"]["suggestions"][0]["model_agreement_count"] == 2
    assert ranked["sections"]["popular"] == []
    assert [item["compatibility_percent"] for item in ranked["sections"]["based_on_cart"]] == sorted(
        [item["compatibility_percent"] for item in ranked["sections"]["based_on_cart"]],
        reverse=True,
    )
    assert all(
        1 <= item["compatibility_percent"] <= 99
        for items in ranked["sections"].values()
        for item in items
    )
    assert all(
        item["probability_percent"] is None
        for items in ranked["sections"].values()
        for item in items
    )
    assert all(
        item["meets_threshold"] == (item["compatibility_percent"] >= 70)
        for model in ranked["models"]
        for item in model["suggestions"]
    )


def test_model_source_identity_is_pure_and_weak_model_does_not_pollute_ensemble():
    live_menu = {"items": [_item(1, 21), _item(2, 22), _item(3, 23)]}
    result = _result(
        based_on_cart=[
            _rec(1, 0.50),
            _rec(2, 0.90, "restaurant_popularity", "cross_sell"),
        ],
        based_on_last_item=[_rec(3, 0.01)],
        popular=[_rec(2, 0.90, "restaurant_popularity", "popular")],
    )

    ranked = _apply_live_recommendation_rules(result, live_menu, [99])
    groups = {group["model_key"]: group for group in ranked["models"]}

    assert [item["item_id"] for item in groups["full_cart"]["suggestions"]] == [1]
    assert "popularity" not in groups
    assert ranked["sections"]["popular"] == []
    assert groups["full_cart"]["threshold_fallback_used"] is False
    assert all(item["meets_threshold"] for item in groups["full_cart"]["suggestions"])
    assert ranked["threshold_fallback_used"] is False
    assert "model_source_mismatch:1" in ranked["warnings"]
    assert "popular_after_cart:1" in ranked["warnings"]


def test_only_last_added_category_is_temporarily_blocked():
    live_menu = {
        "items": [
            _item(10, 5),
            _item(11, 6),
            _item(1, 5),
            _item(2, 6),
            _item(3, 7),
        ],
    }
    result = _result(
        based_on_cart=[_rec(1, 0.90), _rec(2, 0.85), _rec(3, 0.80)],
    )

    ranked = _apply_live_recommendation_rules(
        result,
        live_menu,
        [10, 11],
        last_added_item_id=11,
    )

    assert [item["item_id"] for item in ranked["sections"]["based_on_cart"]] == [1, 3]
    assert "same_category_as_last_item:1" in ranked["warnings"]


def test_previous_visible_item_is_excluded_from_every_model():
    live_menu = {"items": [_item(1, 10), _item(2, 11), _item(3, 12)]}
    result = _result(
        based_on_cart=[_rec(1, 0.90), _rec(2, 0.80)],
        based_on_last_item=[_rec(1, 0.95), _rec(3, 0.75)],
    )

    ranked = _apply_live_recommendation_rules(
        result,
        live_menu,
        [99],
        previous_top_item_id=1,
    )

    assert ranked["top_recommendations"][0]["item_id"] != 1
    assert all(
        item["item_id"] != 1
        for model in ranked["models"]
        for item in model["suggestions"]
    )
    assert "previous_top_excluded:2" in ranked["warnings"]


def test_selected_model_keeps_exact_validated_accuracy_and_combined_provenance():
    live_menu = {
        "items": [_item(99, 1), _item(1, 2), _item(2, 3)],
    }
    result = _result(
        based_on_cart=[_rec(1, 0.80)],
        popular=[
            _rec(1, 0.70, "restaurant_popularity", "popular"),
            _rec(2, 0.90, "restaurant_popularity", "popular"),
        ],
        time_context=[
            _rec(1, 0.75, "time_based", "popular"),
            _rec(2, 0.95, "time_based", "popular"),
        ],
    )
    result.update({
        "selected_model": {
            "model_key": "fbt_confidence",
            "strategy": "fbt_confidence",
            "label_ar": "الارتباط حسب الثقة",
            "validated": True,
            "validation_metric": "recall@5",
            "validation_value": 0.3652590335219852,
            "validation_trials": 2297,
            "validation_scope": "restaurant:192",
            "validation_source": "model_trials/model_comparison.csv",
            "evaluation_version": "v1.1.0@test",
        },
        "time_period_key": "afternoon",
        "time_period_ar": "العصر",
        "selection_policy": "highest_validated_recall_at_fixed_k_then_deterministic_item_rank",
    })
    validation_catalog = {
        "evaluation_version": "v1.1.0@test",
        "empty_cart": {
            "restaurant_popularity": {
                "model_key": "restaurant_popularity",
                "validated": True,
                "validation_metric": "recall@10",
                "validation_value": 0.5698,
                "validation_trials": 67417,
            },
            "time_aware_popularity": {
                "model_key": "time_aware_popularity",
                "validated": True,
                "validation_metric": "recall@10",
                "validation_value": 0.5901,
                "validation_trials": 67417,
                "by_time_period": {
                    "afternoon": {
                        "validated": True,
                        "validation_metric": "recall@10",
                        "validation_value": 0.5139,
                        "validation_trials": 9862,
                    },
                },
            },
        },
    }

    ranked = _apply_live_recommendation_rules(
        result,
        live_menu,
        [99],
        last_added_item_id=99,
        validation_catalog=validation_catalog,
    )

    assert [item["item_id"] for item in ranked["top_recommendations"]] == [1]
    item = ranked["top_recommendations"][0]
    assert item["model_key"] == "fbt_confidence"
    assert item["model_accuracy_percent"] == 36.53
    assert item["accuracy_metric"] == "recall@5"
    assert item["accuracy_validated"] is True
    assert item["time_period_ar"] == "العصر"
    assert [model["model_key"] for model in item["contributing_models"]] == [
        "fbt_confidence",
        "restaurant_popularity",
        "time_aware_popularity",
    ]
    assert item["source_labels_ar"] == [
        "حسب السلة كاملة",
        "الأكثر طلبًا",
        "الأكثر طلبًا في فترة العصر",
    ]
    assert ranked["sections"]["popular"] == []
    assert ranked["sections"]["time_context"] == []
    assert ranked["default_model_key"] == "fbt_confidence"
    assert [model["model_key"] for model in ranked["models"]] == ["fbt_confidence"]


def test_unvalidated_fallback_never_exposes_fake_accuracy():
    live_menu = {"items": [_item(99, 1), _item(1, 2)]}
    result = _result(
        based_on_cart=[_rec(1, 0.25, "live_menu_fallback", "cross_sell")],
    )
    result["selected_model"] = {
        "model_key": "live_menu_fallback",
        "validated": False,
        "validation_metric": None,
        "validation_value": None,
        "validation_trials": 0,
    }

    ranked = _apply_live_recommendation_rules(result, live_menu, [99])
    item = ranked["top_recommendations"][0]

    assert item["accuracy_validated"] is False
    assert item["model_accuracy_percent"] is None
    assert item["accuracy_metric"] is None

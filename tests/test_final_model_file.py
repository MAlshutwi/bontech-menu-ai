import ast
import os
import sys
import tokenize
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.model_loader import final_model_path, load_model  # noqa: E402
from app.model_object import BonTechRecommendationModel  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "ToCoun" / "Final" / "bontech_recommendation_model_v1_1_0.joblib"
RID = 277
OTHER_RID = 172
_MODEL = load_model(MODEL_PATH)
SEED = _MODEL.engine.menu(RID)["default_cart_item_ids"][0]
OTHER_SEED = _MODEL.engine.menu(OTHER_RID)["default_cart_item_ids"][0]


def test_model_file_exists():
    assert MODEL_PATH.exists()
    assert MODEL_PATH == final_model_path(MODEL_PATH)
    assert MODEL_PATH.stat().st_size > 100_000


def test_model_file_loads():
    model = load_model(MODEL_PATH)
    assert isinstance(model, BonTechRecommendationModel)
    assert model.model_version == "hybrid_production_v1.1.0"
    assert len(model.restaurant_items) > 1
    assert RID in model.restaurant_items


def test_portable_model_excludes_customer_profiles():
    model = load_model(MODEL_PATH)
    assert model.customer_profiles == {}
    assert model.engine.profiles == {}
    assert "customer_profiles.parquet" not in model.source_artifacts
    assert model.metadata["privacy"]["contains_customer_profiles"] is False


def test_live_candidates_support_unknown_restaurant_and_avoid_last_category():
    model = load_model(MODEL_PATH)
    live_candidates = [
        {"item_id": 1, "title_ar": "أ", "category_id": 10, "is_available": True},
        {"item_id": 2, "title_ar": "ب", "category_id": 10, "is_available": True},
        {"item_id": 3, "title_ar": "ج", "category_id": 20, "is_available": True},
        {"item_id": 4, "title_ar": "د", "category_id": 30, "is_available": False},
    ]

    result = model.recommend(
        restaurant_id=99999999,
        cart_item_ids=[1],
        last_added_item_id=999,
        live_candidates=live_candidates,
        limit=5,
    )

    assert result["disabled_reason"] is None
    assert result["last_added_item_id"] == 1
    assert result["fallback_used"] is True
    assert result["sections"]["popular"] == []
    assert [item["item_id"] for item in result["sections"]["based_on_cart"]] == [3, 2]
    assert all(
        item["source"] == "live_menu_fallback"
        for item in result["sections"]["based_on_cart"]
    )


def test_sparse_restaurant_cart_uses_menu_fallback_not_popularity_rail():
    model = load_model(MODEL_PATH)
    restaurant_id = next(
        rid
        for rid, items in sorted(model.restaurant_items.items())
        if len(items) > 1 and rid not in model.restaurants_with_fbt
    )
    seed = min(model.restaurant_items[restaurant_id])

    result = model.recommend(
        restaurant_id=restaurant_id,
        cart_item_ids=[seed],
        last_added_item_id=seed,
        limit=5,
    )

    assert result["sections"]["based_on_cart"]
    assert result["sections"]["popular"] == []
    assert all(
        item["source"] != "restaurant_popularity"
        for item in result["sections"]["based_on_cart"]
    )


def test_model_recommend_277_works():
    result = load_model(MODEL_PATH).recommend(
        restaurant_id=RID,
        cart_item_ids=[SEED],
        last_added_item_id=SEED,
        limit=5,
    )
    assert result["restaurant_id"] == RID
    assert result["model_version"] == "hybrid_production_v1.1.0"
    assert result["disabled_reason"] is None
    assert result["top_recommendations"]
    assert result["sections"]["popular"] == []


def test_model_empty_cart_uses_popularity_only_and_rotates():
    model = load_model(MODEL_PATH)
    first = model.recommend(
        restaurant_id=RID,
        cart_item_ids=[],
        limit=5,
    )
    assert first["sections"]["popular"]
    assert first["sections"]["based_on_cart"] == []
    assert first["sections"]["based_on_last_item"] == []
    assert first["sections"]["similar_alternatives"] == []

    first_id = first["top_recommendations"][0]["item_id"]
    second = model.recommend(
        restaurant_id=RID,
        cart_item_ids=[],
        previous_top_item_id=first_id,
        limit=5,
    )
    assert all(item["item_id"] != first_id for item in second["top_recommendations"])
    assert all(item["item_id"] != first_id for item in second["sections"]["popular"])


def test_model_rotation_with_limit_one_fetches_the_next_candidate():
    model = load_model(MODEL_PATH)
    first = model.recommend(
        restaurant_id=182,
        cart_item_ids=[1280],
        last_added_item_id=1280,
        limit=5,
    )
    first_id = first["top_recommendations"][0]["item_id"]

    rotated = model.recommend(
        restaurant_id=182,
        cart_item_ids=[1280],
        last_added_item_id=1280,
        previous_top_item_id=first_id,
        limit=1,
    )

    assert rotated["top_recommendations"]
    assert rotated["top_recommendations"][0]["item_id"] != first_id


def test_model_recommend_other_restaurant_works():
    result = load_model(MODEL_PATH).recommend(
        restaurant_id=OTHER_RID,
        cart_item_ids=[OTHER_SEED],
        last_added_item_id=OTHER_SEED,
        limit=5,
    )
    assert result["restaurant_id"] == OTHER_RID
    assert result["disabled_reason"] is None
    assert result["top_recommendations"]


def test_unknown_restaurant_safe():
    result = load_model(MODEL_PATH).recommend(
        restaurant_id=99999999,
        cart_item_ids=[SEED],
        last_added_item_id=SEED,
        limit=5,
    )
    assert result["fallback_used"] is True
    assert result["disabled_reason"] == "unknown_restaurant_or_no_menu_artifacts"
    assert result["top_recommendations"] == []
    assert all(items == [] for items in result["sections"].values())


def test_no_cross_restaurant_addable_leakage():
    model = load_model(MODEL_PATH)
    result = model.recommend(
        restaurant_id=OTHER_RID,
        cart_item_ids=[OTHER_SEED],
        last_added_item_id=OTHER_SEED,
        limit=10,
    )
    available = model.restaurant_items[OTHER_RID]
    for items in result["sections"].values():
        for item in items:
            assert item["addable"] is True
            assert item["item_id"] in available
    for item in result["top_recommendations"]:
        assert item["addable"] is True
        assert item["item_id"] in available


def test_api_uses_model_file(monkeypatch):
    from app import main as main_module

    calls = []
    original = main_module.load_model
    menu_items = [{
        "item_id": item_id,
        "restaurant_id": RID,
        "title_ar": _MODEL.engine.titles.get(item_id, ("", ""))[1],
        "title_en": _MODEL.engine.titles.get(item_id, ("", ""))[0],
        "category_id": _MODEL.engine.item_category.get(item_id),
        "category_ar": "",
        "category_en": "",
        "is_available": True,
        "availability_reason": "always_available",
        "sizes": [],
    } for item_id in sorted(_MODEL.restaurant_items[RID])]

    def wrapped_load_model(*args, **kwargs):
        calls.append(final_model_path())
        return original(*args, **kwargs)

    monkeypatch.setattr(main_module, "load_model", wrapped_load_model)
    monkeypatch.setattr(main_module, "_live_menu_payload", lambda *args, **kwargs: {
        "restaurant": {"restaurant_id": RID, "name": "Test", "name_ar": "اختبار"},
        "items": menu_items,
        "categories": [],
        "count": len(menu_items),
        "source": "live_database",
        "include_inactive": False,
    })
    with TestClient(main_module.app) as client:
        response = client.post("/api/v1/recommendations", json={
            "restaurant_id": RID,
            "cart_item_ids": [SEED],
            "last_added_item_id": SEED,
            "limit": 5,
        })
    assert response.status_code == 200
    assert calls
    assert all(path == MODEL_PATH.resolve() for path in calls)
    assert response.json()["model_version"] == "hybrid_production_v1.1.0"


def test_final_model_widget_files_exist():
    widget_dir = ROOT / "delivery" / "final_model" / "widget"
    assert (widget_dir / "smart-suggestions-widget.js").exists()
    assert (widget_dir / "smart-suggestions-widget.css").exists()
    assert (widget_dir / "example-integration.html").exists()
    js = (widget_dir / "smart-suggestions-widget.js").read_text(encoding="utf-8")
    assert "apiBaseUrl" in js
    assert "restaurantId" in js
    assert "277" not in js


def test_no_env_or_secret_files_in_final_model_delivery():
    final_model_dir = ROOT / "delivery" / "final_model"
    assert not list(final_model_dir.rglob(".env"))
    blocked_names = {"db_dump.sql", "database.dump", "secrets.json"}
    assert not [p for p in final_model_dir.rglob("*") if p.name.lower() in blocked_names]


def test_no_sensitive_or_generated_wording_in_final_model_text():
    final_model_dir = ROOT / "delivery" / "final_model"
    text_files = [
        *final_model_dir.rglob("*.md"),
        *final_model_dir.rglob("*.js"),
        *final_model_dir.rglob("*.css"),
        *final_model_dir.rglob("*.html"),
    ]
    blocked = [
        "password",
        "database_url",
        "c:\\",
        "/users/",
        "open" + "ai",
        "g" + "pt",
        "pr" + "ompt",
        "l" + "lm",
        "generated by",
    ]
    for path in text_files:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for term in blocked:
            assert term not in text, f"{term} found in {path}"


def test_no_arabic_code_comments_or_docstrings():
    paths = [
        *list((ROOT / "app").rglob("*.py")),
        ROOT / "scripts" / "build_final_model_file.py",
    ]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        docstrings = []
        module_doc = ast.get_docstring(tree)
        if module_doc:
            docstrings.append(module_doc)
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node)
                if doc:
                    docstrings.append(doc)
        for doc in docstrings:
            assert not _has_arabic(doc), f"Arabic docstring in {path}"
        with tokenize.open(path) as handle:
            for token in tokenize.generate_tokens(handle.readline):
                if token.type == tokenize.COMMENT:
                    assert not _has_arabic(token.string), f"Arabic comment in {path}:{token.start[0]}"


def _has_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)

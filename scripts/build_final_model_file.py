"""
Build the single serialized BonTech recommendation model file from current artifacts.

This script does not train or tune a model. It loads the existing runtime artifacts
through the production recommender, wraps the loaded in-memory state in
BonTechRecommendationModel, and serializes that object for API serving.
"""
from __future__ import annotations

import json
import os
import sys
import csv
from pathlib import Path

import joblib


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import ARTIFACTS  # noqa: E402
from app.model_loader import DEFAULT_MODEL_PATH  # noqa: E402
from app.model_object import BonTechRecommendationModel  # noqa: E402
from app.recommender import Recommender  # noqa: E402


RUNTIME_ARTIFACTS = [
    "fbt_pairs.parquet",
    "global_popularity.parquet",
    "item_recency.parquet",
    "item_scope_mapping.parquet",
    "model_metadata.json",
    "restaurant_items.parquet",
    "restaurant_popularity.parquet",
    "phase10/time_aware_popularity.parquet",
    "phase10/similar_alternatives.parquet",
    "phase10/pooled_fbt_fallback.parquet",
]

PRIMARY_TRAINED_ARTIFACTS = [
    "fbt_pairs.parquet",
    "item_scope_mapping.parquet",
    "restaurant_items.parquet",
    "restaurant_popularity.parquet",
]

VALIDATION_ARTIFACTS = [
    "model_trials/model_comparison.csv",
    "model_trials/context_results.json",
]

SERVABLE_CART_STRATEGIES = (
    "fbt_confidence",
    "fbt_hybrid",
    "fbt_paircount",
    "fbt_lift",
)


def _read_metadata() -> dict:
    path = ARTIFACTS / "model_metadata.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _bounded_metric(value) -> float | None:
    try:
        metric = float(value)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= metric <= 1.0:
        return None
    return metric


def _validation_record(strategy: str, row: dict, *, scope: str, source: str) -> dict:
    recall5 = _bounded_metric(row.get("recall@5"))
    recall10 = _bounded_metric(row.get("recall@10"))
    trials = max(0, int(float(row.get("test_trials") or 0)))
    return {
        "model_key": strategy,
        "strategy": strategy,
        "validated": recall5 is not None and trials > 0,
        "validation_metric": "recall@5" if recall5 is not None else None,
        "validation_value": recall5,
        "validation_trials": trials,
        "secondary_metric": "recall@10" if recall10 is not None else None,
        "secondary_value": recall10,
        "validation_scope": scope,
        "validation_source": source,
    }


def _weighted_strategy_records(rows: list[dict]) -> dict[str, dict]:
    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        strategy = str(row.get("strategy") or "")
        trials = max(0.0, float(row.get("test_trials") or 0.0))
        recall5 = _bounded_metric(row.get("recall@5"))
        recall10 = _bounded_metric(row.get("recall@10"))
        if not strategy or trials <= 0 or recall5 is None or recall10 is None:
            continue
        total = totals.setdefault(strategy, {"trials": 0.0, "recall5": 0.0, "recall10": 0.0})
        total["trials"] += trials
        total["recall5"] += trials * recall5
        total["recall10"] += trials * recall10
    records = {}
    for strategy, total in totals.items():
        if total["trials"] <= 0:
            continue
        records[strategy] = _validation_record(
            strategy,
            {
                "test_trials": total["trials"],
                "recall@5": total["recall5"] / total["trials"],
                "recall@10": total["recall10"] / total["trials"],
            },
            scope="global_micro_average",
            source="model_trials/model_comparison.csv",
        )
    return records


def _build_validated_model_selection(metadata: dict) -> dict:
    comparison_path = ARTIFACTS / "model_trials" / "model_comparison.csv"
    with comparison_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    global_records = _weighted_strategy_records(rows)
    by_restaurant: dict[str, dict] = {}
    restaurant_rows: dict[int, list[dict]] = {}
    for row in rows:
        strategy = str(row.get("strategy") or "")
        if strategy not in SERVABLE_CART_STRATEGIES:
            continue
        try:
            restaurant_id = int(row.get("restaurant_id"))
        except (TypeError, ValueError):
            continue
        restaurant_rows.setdefault(restaurant_id, []).append(row)

    for restaurant_id, candidates in sorted(restaurant_rows.items()):
        valid = [
            row for row in candidates
            if _bounded_metric(row.get("recall@5")) is not None
            and int(float(row.get("test_trials") or 0)) > 0
        ]
        if not valid:
            continue
        best = max(
            valid,
            key=lambda row: (
                float(row["recall@5"]),
                float(row.get("recall@10") or 0.0),
                -SERVABLE_CART_STRATEGIES.index(str(row["strategy"])),
            ),
        )
        by_restaurant[str(restaurant_id)] = _validation_record(
            str(best["strategy"]),
            best,
            scope=f"restaurant:{restaurant_id}",
            source="model_trials/model_comparison.csv",
        )

    globally_valid = [
        record for strategy, record in global_records.items()
        if strategy in SERVABLE_CART_STRATEGIES and record["validated"]
    ]
    global_cart = max(
        globally_valid,
        key=lambda record: (
            record["validation_value"],
            record["secondary_value"],
            -SERVABLE_CART_STRATEGIES.index(record["strategy"]),
        ),
    )

    context_path = ARTIFACTS / "model_trials" / "context_results.json"
    context_results = json.loads(context_path.read_text(encoding="utf-8"))
    overall = context_results.get("overall_context_models") or {}
    period_results = context_results.get("by_time_bucket") or {}
    total_trials = sum(max(0, int(values.get("n") or 0)) for values in period_results.values())

    def time_record(model_key: str, metric_key: str, *, source_suffix: str) -> dict:
        value = _bounded_metric(overall.get(metric_key))
        return {
            "model_key": model_key,
            "strategy": metric_key,
            "validated": value is not None and total_trials > 0,
            "validation_metric": "recall@10" if value is not None else None,
            "validation_value": value,
            "validation_trials": total_trials,
            "validation_scope": "global_temporal_holdout",
            "validation_source": f"model_trials/context_results.json#{source_suffix}",
        }

    time_aware = time_record(
        "time_aware_popularity",
        "time_aware_popularity",
        source_suffix="overall_context_models.time_aware_popularity",
    )
    time_aware["by_time_period"] = {}
    for period, values in sorted(period_results.items()):
        value = _bounded_metric(values.get("time_aware_popularity"))
        trials = max(0, int(values.get("n") or 0))
        time_aware["by_time_period"][period] = {
            "validation_metric": "recall@10" if value is not None else None,
            "validation_value": value,
            "validation_trials": trials,
            "validation_scope": f"time_period:{period}",
            "validation_source": f"model_trials/context_results.json#by_time_bucket.{period}.time_aware_popularity",
            "validated": value is not None and trials > 0,
        }

    popularity = time_record(
        "restaurant_popularity",
        "popularity",
        source_suffix="overall_context_models.popularity",
    )
    similarity = dict(global_records.get("item2vec") or {
        "model_key": "item2vec",
        "strategy": "item2vec",
        "validated": False,
        "validation_metric": None,
        "validation_value": None,
        "validation_trials": 0,
        "validation_scope": "unavailable",
        "validation_source": None,
    })

    return {
        "evaluation_version": (
            f"{metadata.get('model_version', 'unknown')}@{metadata.get('generated_at', 'unknown')}"
        ),
        "selection_policy": "highest_validated_recall_at_fixed_k_then_deterministic_item_rank",
        "cart": {
            "metric": "recall@5",
            "global": global_cart,
            "by_restaurant": by_restaurant,
            "eligible_strategies": list(SERVABLE_CART_STRATEGIES),
        },
        "empty_cart": {
            "time_aware_popularity": time_aware,
            "restaurant_popularity": popularity,
        },
        "supporting_models": {
            "item2vec": similarity,
            "restaurant_popularity": popularity,
            "time_aware_popularity": time_aware,
        },
        "personalized": {
            "model_key": "personalized",
            "validated": False,
            "validation_metric": None,
            "validation_value": None,
            "validation_trials": 0,
            "validation_scope": "unavailable",
            "validation_source": None,
            "unavailable_reason": "insufficient_validated_customer_order_linkage",
        },
    }


def _validate_artifacts() -> None:
    missing = [
        rel for rel in [*RUNTIME_ARTIFACTS, *VALIDATION_ARTIFACTS]
        if not (ARTIFACTS / rel).exists()
    ]
    if missing:
        raise FileNotFoundError(f"Missing runtime artifacts: {missing}")

    clean_cache = ARTIFACTS / "_clean_lines.parquet"
    if clean_cache.exists():
        stale = [
            rel
            for rel in PRIMARY_TRAINED_ARTIFACTS
            if (ARTIFACTS / rel).stat().st_mtime < clean_cache.stat().st_mtime
        ]
        if stale:
            raise RuntimeError(
                "Training artifacts are older than the cleaned transaction cache; "
                f"regenerate them before packaging: {stale}"
            )


def _validate_packaged_model(model: BonTechRecommendationModel) -> None:
    if model.customer_profiles or getattr(model.engine, "profiles", None):
        raise RuntimeError("Portable model unexpectedly contains customer profiles")
    if model.model_version != model.engine.model_version:
        raise RuntimeError("Wrapper and engine model versions do not match")
    if model.artifact_schema_version != model.schema_version:
        raise RuntimeError("Unexpected portable model schema version")


def build_model_file(output_path: str | Path | None = None) -> Path:
    _validate_artifacts()
    engine = Recommender(load_customer_profiles=False)
    metadata = _read_metadata()
    metadata["validated_model_selection"] = _build_validated_model_selection(metadata)
    metadata["privacy"] = {
        "contains_customer_profiles": False,
        "customer_level_data_included": False,
    }
    model = BonTechRecommendationModel.from_engine(
        engine=engine,
        metadata=metadata,
        source_artifacts=[*RUNTIME_ARTIFACTS, *VALIDATION_ARTIFACTS],
    )
    _validate_packaged_model(model)
    out = Path(output_path) if output_path else DEFAULT_MODEL_PATH
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    joblib.dump(model, tmp, compress=3)
    os.replace(tmp, out)
    _validate_packaged_model(joblib.load(out))
    return out


def main() -> int:
    path = build_model_file()
    model = joblib.load(path)
    print(f"final_model_file={path}")
    print(f"model_version={model.model_version}")
    print(f"restaurants_with_items={len(model.restaurant_items)}")
    print(f"restaurants_with_fbt={len(model.restaurants_with_fbt)}")
    print(f"restaurants_with_popularity={len(model.restaurants_with_popularity)}")
    print(f"size_bytes={path.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

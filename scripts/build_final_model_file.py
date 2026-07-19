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


def _read_metadata() -> dict:
    path = ARTIFACTS / "model_metadata.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_artifacts() -> None:
    missing = [rel for rel in RUNTIME_ARTIFACTS if not (ARTIFACTS / rel).exists()]
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
    metadata["privacy"] = {
        "contains_customer_profiles": False,
        "customer_level_data_included": False,
    }
    model = BonTechRecommendationModel.from_engine(
        engine=engine,
        metadata=metadata,
        source_artifacts=RUNTIME_ARTIFACTS,
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

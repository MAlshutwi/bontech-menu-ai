"""
Loader for the serialized BonTech recommendation model file.
"""
from __future__ import annotations

import os
from pathlib import Path

import joblib

from .config import ROOT
from .model_object import BonTechRecommendationModel


MODEL_FILE_NAME = "bontech_recommendation_model_v1_1_0.joblib"
# Canonical, easy-to-transfer model location.  Override with BONTECH_MODEL_FILE
# only when a deployment intentionally uses a different artifact.
DEFAULT_MODEL_PATH = ROOT / "ToCoun" / "Final" / MODEL_FILE_NAME
_MODEL_CACHE: dict[Path, BonTechRecommendationModel] = {}


def final_model_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).resolve()
    return Path(os.environ.get("BONTECH_MODEL_FILE", DEFAULT_MODEL_PATH)).resolve()


def load_model(path: str | Path | None = None) -> BonTechRecommendationModel:
    model_path = final_model_path(path)
    cached = _MODEL_CACHE.get(model_path)
    if cached is not None:
        return cached
    if not model_path.exists():
        raise FileNotFoundError(f"BonTech recommendation model file not found: {model_path}")
    model = joblib.load(model_path)
    if not isinstance(model, BonTechRecommendationModel):
        raise TypeError(f"Unexpected model object type: {type(model)!r}")
    _MODEL_CACHE[model_path] = model
    return model


def reset_model_cache() -> None:
    _MODEL_CACHE.clear()

"""
Loader for the serialized BonTech recommendation model file.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from threading import Lock

import joblib

from .config import ROOT
from .model_object import BonTechRecommendationModel


MODEL_FILE_NAME = "bontech_recommendation_model_v1_1_0.joblib"
# Canonical, easy-to-transfer model location.  Override with BONTECH_MODEL_FILE
# only when a deployment intentionally uses a different artifact.
DEFAULT_MODEL_PATH = ROOT / "ToCoun" / "Final" / MODEL_FILE_NAME
_MODEL_CACHE: dict[Path, tuple[tuple[int, int], BonTechRecommendationModel]] = {}
_MODEL_CACHE_LOCK = Lock()


def final_model_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).resolve()
    return Path(os.environ.get("BONTECH_MODEL_FILE", DEFAULT_MODEL_PATH)).resolve()


def load_model(path: str | Path | None = None) -> BonTechRecommendationModel:
    model_path = final_model_path(path)
    if not model_path.exists():
        raise FileNotFoundError(f"BonTech recommendation model file not found: {model_path}")
    stat = model_path.stat()
    fingerprint = (stat.st_mtime_ns, stat.st_size)
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(model_path)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        _verify_optional_checksum(model_path)
        model = joblib.load(model_path)
        _validate_model(model)
        _MODEL_CACHE[model_path] = (fingerprint, model)
        return model


def _verify_optional_checksum(model_path: Path) -> None:
    expected = os.environ.get("BONTECH_MODEL_SHA256", "").strip().lower()
    if not expected:
        return
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise RuntimeError("BONTECH_MODEL_SHA256 must be a 64-character hexadecimal digest")
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if not hmac.compare_digest(digest, expected):
        raise RuntimeError("BonTech recommendation model checksum mismatch")


def _validate_model(model: object) -> None:
    if not isinstance(model, BonTechRecommendationModel):
        raise TypeError(f"Unexpected model object type: {type(model)!r}")
    if getattr(model, "artifact_schema_version", None) != BonTechRecommendationModel.schema_version:
        raise RuntimeError("Unsupported BonTech recommendation model schema")
    if model.model_version != getattr(model.engine, "model_version", None):
        raise RuntimeError("Wrapper and engine model versions do not match")
    if getattr(model, "customer_profiles", None) or getattr(model.engine, "profiles", None):
        raise RuntimeError("Portable model must not contain customer profiles")


def reset_model_cache() -> None:
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()

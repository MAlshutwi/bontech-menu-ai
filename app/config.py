"""
app/config.py - central configuration source: local environment plus config.yaml.
Never prints secrets. Imported by scripts and the API service.
"""
from __future__ import annotations
import os
from pathlib import Path
from urllib.parse import quote_plus
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
# The portable ToCoun delivery keeps its connection settings beside the model.
# Root .env values remain authoritative when both files define the same key.
load_dotenv(ROOT / "ToCoun" / ".env")

# Project paths
ARTIFACTS = ROOT / "artifacts"
REPORTS = ROOT / "reports"
ARTIFACTS.mkdir(exist_ok=True)
REPORTS.mkdir(exist_ok=True)

# Load config.yaml
with open(ROOT / "config.yaml", "r", encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)

MODEL_VERSION: str = CFG.get("model_version", "v0")
CLEANING: dict = CFG["cleaning"]
THRESHOLDS: dict = CFG["thresholds"]
FBT: dict = CFG["fbt"]
NORM: dict = CFG["normalization"]
EVAL: dict = CFG["evaluation"]
HYBRID_WEIGHTS: dict = CFG["hybrid_weights"]
CUSTOMER: dict = CFG["customer"]
SERVING: dict = CFG["serving"]
AB_TESTING: dict = CFG.get("ab_testing", {"enabled": False, "variants": {}})
PHASE09: dict = CFG.get("phase09", {})
PHASE10: dict = CFG.get("phase10", {})

# --- Database connection values from environment only. ---
_DB = {
    "host": os.environ.get("DB_HOST"),
    "port": os.environ.get("DB_PORT"),
    "name": os.environ.get("DB_NAME"),
    "user": os.environ.get("DB_USER"),
    "password": os.environ.get("DB_PASS"),
}


def db_url() -> str:
    """Build a SQLAlchemy URL with safe credential encoding."""
    missing = [k for k, v in _DB.items() if not v]
    if missing:
        raise RuntimeError(f"Missing DB env vars: {missing}")
    return (
        f"postgresql+psycopg2://{quote_plus(_DB['user'])}:{quote_plus(_DB['password'])}"
        f"@{_DB['host']}:{_DB['port']}/{_DB['name']}"
    )


def safe_db_info() -> str:
    """Return connection metadata without credentials for logs."""
    return f"{_DB['host']}:{_DB['port']}/{_DB['name']} (user={_DB['user']})"

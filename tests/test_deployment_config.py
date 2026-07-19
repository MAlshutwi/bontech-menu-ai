import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "ToCoun" / "Final" / "bontech_recommendation_model_v1_1_0.joblib"


def test_render_blueprint_waits_for_ci_and_verifies_model():
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    service = blueprint["services"][0]
    env = {entry["key"]: entry.get("value") for entry in service["envVars"]}

    assert service["runtime"] == "docker"
    assert service["autoDeployTrigger"] == "checksPass"
    assert service["healthCheckPath"] == "/health"
    assert env["PGSSLMODE"] == "require"
    assert env["BONTECH_MODEL_SHA256"] == hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()


def test_container_and_frontend_dependencies_are_reproducible():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    package = json.loads(
        (ROOT / "ToCoun" / "LovableMenuAI" / "package.json").read_text(encoding="utf-8")
    )

    assert dockerfile.count("@sha256:") == 2
    assert "USER bontech" in dockerfile
    assert "COPY --chown=bontech:bontech . ." in dockerfile
    versions = {**package["dependencies"], **package["devDependencies"]}
    assert versions
    assert all(version != "latest" for version in versions.values())


def test_ci_runs_backend_frontend_and_container_builds():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python -m pytest tests -q" in workflow
    assert "npm run build" in workflow
    assert "docker/build-push-action" in workflow

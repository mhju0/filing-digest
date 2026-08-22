"""Contracts for the product boundaries exercised by GitHub Actions."""

from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def _commands(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_backend_ci_exercises_postgresql_boundary() -> None:
    backend = _workflow()["jobs"]["backend"]

    postgres = backend["services"]["postgres"]
    assert postgres["image"] == "pgvector/pgvector:pg16"
    assert "5432:5432" in postgres["ports"]
    assert "pg_isready" in postgres["options"]

    commands = _commands(backend)
    assert "db/init.sql" in commands
    assert "tests/fixtures/ci_smoke_seed.sql" in commands
    assert "tests/test_smoke.py" in commands


def test_ios_ci_builds_app_and_runs_unit_tests() -> None:
    ios = _workflow()["jobs"]["ios"]

    assert ios["runs-on"] == "macos-15"
    commands = _commands(ios)
    assert "xcodebuild test" in commands
    assert "FilingDigest.xcodeproj" in commands
    assert "-scheme FilingDigest" in commands
    assert "platform=iOS Simulator" in commands

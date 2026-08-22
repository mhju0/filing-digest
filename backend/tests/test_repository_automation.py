"""Contracts for repository-level dependency automation."""

from pathlib import Path

import yaml

DEPENDABOT_PATH = Path(__file__).parents[2] / ".github" / "dependabot.yml"


def test_dependabot_covers_backend_and_github_actions() -> None:
    config = yaml.safe_load(DEPENDABOT_PATH.read_text())

    updates = {
        (entry["package-ecosystem"], entry["directory"]): entry
        for entry in config["updates"]
    }
    assert config["version"] == 2
    assert ("pip", "/backend") in updates
    assert ("github-actions", "/") in updates
    assert updates[("pip", "/backend")]["schedule"]["interval"] == "weekly"
    assert updates[("github-actions", "/")]["schedule"]["interval"] == "weekly"

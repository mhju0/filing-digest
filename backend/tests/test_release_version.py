"""Release-version consistency across the backend, iOS app, and public docs."""

import re
import tomllib
from pathlib import Path

from app import __version__
from app.config import Settings

REPOSITORY_ROOT = Path(__file__).parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"


def test_product_release_version_is_consistent() -> None:
    pyproject = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text())
    release_version = pyproject["project"]["version"]

    assert __version__ == release_version
    assert (
        Settings.model_fields["sec_user_agent"].default
        == f"filing-digest/{release_version} your-contact@example.com"
    )
    assert (
        f"SEC_USER_AGENT=filing-digest/{release_version} your-contact@example.com"
        in (BACKEND_ROOT / ".env.example").read_text()
    )
    assert (
        f"**Status:** v{release_version}"
        in (REPOSITORY_ROOT / "README.md").read_text()
    )
    assert (
        f"v{release_version} portfolio architecture"
        in (REPOSITORY_ROOT / "docs" / "ARCHITECTURE.md").read_text()
    )
    project = (
        REPOSITORY_ROOT / "ios" / "FilingDigest.xcodeproj" / "project.pbxproj"
    ).read_text()
    marketing_versions = set(
        re.findall(r"MARKETING_VERSION = ([^;]+);", project)
    )
    assert marketing_versions == {release_version}

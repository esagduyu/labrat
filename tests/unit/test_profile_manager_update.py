"""ProfileManager.update replaces a profile in place without touching secrets."""

from pathlib import Path

import pytest

from labrat.profile.manager import ProfileError, ProfileManager
from labrat.profile.model import Profile


def _mgr(tmp_path: Path) -> ProfileManager:
    return ProfileManager(profiles_path=tmp_path / "profiles.json")


def test_update_replaces_existing_profile(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    mgr.add(Profile(name="p1", dialect="duckdb", path="/tmp/a.duckdb"))
    updated = mgr.get("p1").model_copy(update={"verify_enabled": True})
    mgr.update(updated)
    assert mgr.get("p1").verify_enabled is True


def test_update_unknown_profile_raises(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    with pytest.raises(ProfileError):
        mgr.update(Profile(name="ghost", dialect="duckdb"))

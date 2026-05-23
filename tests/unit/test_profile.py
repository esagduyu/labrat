"""Unit tests for profile model, storage, and manager."""

import json
from pathlib import Path

import pytest

from labrat.profile.manager import ProfileError, ProfileManager, make_profile
from labrat.profile.model import Profile


@pytest.fixture()
def tmp_profiles(tmp_path: Path) -> Path:
    return tmp_path / "profiles.json"


def _manager(tmp_profiles: Path) -> ProfileManager:
    return ProfileManager(profiles_path=tmp_profiles)


def test_add_and_list(tmp_profiles: Path) -> None:
    mgr = _manager(tmp_profiles)
    p = make_profile("local-duck", "duckdb", path=":memory:")
    mgr.add(p)
    profiles = mgr.list_all()
    assert len(profiles) == 1
    assert profiles[0].name == "local-duck"


def test_add_duplicate_raises(tmp_profiles: Path) -> None:
    mgr = _manager(tmp_profiles)
    p = make_profile("dup", "duckdb")
    mgr.add(p)
    with pytest.raises(ProfileError, match="already exists"):
        mgr.add(p)


def test_remove(tmp_profiles: Path) -> None:
    mgr = _manager(tmp_profiles)
    p = make_profile("todelete", "duckdb")
    mgr.add(p)
    mgr.remove("todelete")
    assert mgr.list_all() == []


def test_remove_nonexistent(tmp_profiles: Path) -> None:
    mgr = _manager(tmp_profiles)
    with pytest.raises(ProfileError, match="not found"):
        mgr.remove("ghost")


def test_get_nonexistent(tmp_profiles: Path) -> None:
    mgr = _manager(tmp_profiles)
    with pytest.raises(ProfileError, match="not found"):
        mgr.get("ghost")


def test_secrets_not_in_json(tmp_profiles: Path) -> None:
    """Passwords must never appear in the JSON profiles file."""
    mgr = _manager(tmp_profiles)
    p = make_profile("secret-test", "duckdb", has_secret=True)
    mgr.add(p, secret="supersecret")

    raw = json.loads(tmp_profiles.read_text())
    for entry in raw:
        for v in entry.values():
            assert v != "supersecret", "Secret leaked into profiles JSON"


def test_profile_validation_bad_name() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Profile(name="bad name!", dialect="duckdb")


def test_test_connection_duckdb(tmp_profiles: Path) -> None:
    """Connection test succeeds against in-memory DuckDB."""
    mgr = _manager(tmp_profiles)
    p = make_profile("inmem", "duckdb", path=":memory:", is_read_only=False)
    mgr.add(p)
    assert mgr.test_connection("inmem") is True


def test_test_connection_bad_path(tmp_profiles: Path) -> None:
    """Connection test fails gracefully for a non-existent DuckDB file."""
    mgr = _manager(tmp_profiles)
    p = make_profile("bad-path", "duckdb", path="/nonexistent/path.duckdb")
    mgr.add(p)
    assert mgr.test_connection("bad-path") is False

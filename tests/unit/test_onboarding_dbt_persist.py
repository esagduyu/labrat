"""app.py::_save_onboarding_result persists dbt_project_path when catalog_type=='dbt'."""

from pathlib import Path

import pytest

from labrat.app import LabRatApp
from labrat.profile import storage
from labrat.profile.manager import ProfileManager
from labrat.screens.onboarding import OnboardingResult


@pytest.fixture(autouse=True)
def _isolated_profiles_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_save_onboarding_result uses ProfileManager() with no path override;
    redirect storage's default path so the test never touches real user data."""
    profiles_path = tmp_path / "profiles.json"
    monkeypatch.setattr(storage, "_profiles_path", lambda: profiles_path)


def test_dbt_catalog_persists_dbt_project_path() -> None:
    result = OnboardingResult(
        profile_name="p1",
        dialect="duckdb",
        path="/tmp/x.duckdb",
        catalog_type="dbt",
        catalog_path="/repo/dbt",
    )
    LabRatApp()._save_onboarding_result(result)
    profile = ProfileManager().get("p1")
    assert profile.dbt_project_path == "/repo/dbt"


def test_non_dbt_catalog_leaves_dbt_project_path_none() -> None:
    result = OnboardingResult(
        profile_name="p2",
        dialect="duckdb",
        path="/tmp/y.duckdb",
        catalog_type=None,
        catalog_path=None,
    )
    LabRatApp()._save_onboarding_result(result)
    profile = ProfileManager().get("p2")
    assert profile.dbt_project_path is None

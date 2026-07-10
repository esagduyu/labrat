"""Profile.dbt_project_path: legacy-safe field + make_profile passthrough."""

from labrat.profile.manager import make_profile
from labrat.profile.model import Profile


def test_field_defaults_none_and_legacy_validates() -> None:
    assert Profile(name="p", dialect="duckdb").dbt_project_path is None
    legacy = {"name": "old", "dialect": "duckdb", "path": "/tmp/x.duckdb"}
    assert Profile.model_validate(legacy).dbt_project_path is None


def test_make_profile_passthrough() -> None:
    p = make_profile(name="p", dialect="duckdb", dbt_project_path="/repo/dbt")
    assert p.dbt_project_path == "/repo/dbt"

# tests/unit/test_profile_model_settings.py
"""Profile gains agent/harvest/verify settings fields (TUI M1)."""

from labrat.profile.model import Profile


def test_new_fields_have_safe_defaults() -> None:
    p = Profile(name="p1", dialect="duckdb")
    assert p.agent_provider == "auto"
    assert p.agent_model is None
    assert p.harvest_opt_in is False
    assert p.verify_enabled is False


def test_deserializes_legacy_profile_without_new_fields() -> None:
    # A profile serialized before these fields existed must still validate.
    legacy = {"name": "old", "dialect": "duckdb", "path": "/tmp/x.duckdb"}
    p = Profile.model_validate(legacy)
    assert p.agent_provider == "auto"
    assert p.harvest_opt_in is False


def test_fields_round_trip() -> None:
    p = Profile(
        name="p2",
        dialect="duckdb",
        agent_provider="anthropic",
        agent_model="claude-sonnet-4-6",
        harvest_opt_in=True,
        verify_enabled=True,
    )
    again = Profile.model_validate(p.model_dump())
    assert again.agent_provider == "anthropic"
    assert again.verify_enabled is True

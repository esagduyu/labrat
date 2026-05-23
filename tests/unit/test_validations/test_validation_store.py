"""Tests for ValidationRuleStore (M32)."""

from __future__ import annotations

from pathlib import Path

from labrat.validations.model import ValidationRule, ValidationSeverity


def _rule(profile: str = "dev", enabled: bool = True, text: str = "no nulls") -> ValidationRule:
    return ValidationRule(
        profile=profile,
        natural_language_rule=text,
        severity=ValidationSeverity.warn,
        enabled=enabled,
    )


def test_store_save_and_read(tmp_path: Path) -> None:
    from labrat.validations.store import ValidationRuleStore

    store = ValidationRuleStore(rules_dir=tmp_path)
    rule = _rule()
    store.save(rule)
    rules = store.read_profile("dev")
    assert len(rules) == 1
    assert rules[0].id == rule.id


def test_store_read_only_given_profile(tmp_path: Path) -> None:
    from labrat.validations.store import ValidationRuleStore

    store = ValidationRuleStore(rules_dir=tmp_path)
    store.save(_rule(profile="dev", text="dev rule"))
    store.save(_rule(profile="prod", text="prod rule"))
    dev_rules = store.read_profile("dev")
    assert len(dev_rules) == 1
    assert dev_rules[0].natural_language_rule == "dev rule"


def test_store_delete_removes_rule(tmp_path: Path) -> None:
    from labrat.validations.store import ValidationRuleStore

    store = ValidationRuleStore(rules_dir=tmp_path)
    rule = _rule()
    store.save(rule)
    store.delete(profile="dev", rule_id=rule.id)
    assert store.read_profile("dev") == []


def test_store_update_enabled_flag(tmp_path: Path) -> None:
    from labrat.validations.store import ValidationRuleStore

    store = ValidationRuleStore(rules_dir=tmp_path)
    rule = _rule(enabled=True)
    store.save(rule)
    store.set_enabled(profile="dev", rule_id=rule.id, enabled=False)
    updated = store.read_profile("dev")[0]
    assert updated.enabled is False


def test_store_active_rules_filters_disabled(tmp_path: Path) -> None:
    from labrat.validations.store import ValidationRuleStore

    store = ValidationRuleStore(rules_dir=tmp_path)
    store.save(_rule(enabled=True, text="active"))
    store.save(_rule(enabled=False, text="inactive"))
    active = store.active_rules("dev")
    assert len(active) == 1
    assert active[0].natural_language_rule == "active"


def test_store_file_is_jsonl(tmp_path: Path) -> None:
    from labrat.validations.store import ValidationRuleStore

    store = ValidationRuleStore(rules_dir=tmp_path)
    store.save(_rule())
    jsonl = tmp_path / "dev.jsonl"
    assert jsonl.exists()
    lines = jsonl.read_text().strip().splitlines()
    assert len(lines) == 1

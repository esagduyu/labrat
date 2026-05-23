"""Tests for ValidationRule model (M32)."""

from __future__ import annotations

from labrat.validations.model import ValidationRule, ValidationSeverity


def _rule(**kw: object) -> ValidationRule:
    defaults: dict[str, object] = {
        "profile": "dev",
        "natural_language_rule": "WAU should never exceed 10 billion",
        "severity": ValidationSeverity.warn,
    }
    defaults.update(kw)
    return ValidationRule(**defaults)  # type: ignore[arg-type]


def test_rule_has_required_fields() -> None:
    rule = _rule()
    assert rule.profile == "dev"
    assert "WAU" in rule.natural_language_rule
    assert rule.severity == ValidationSeverity.warn


def test_rule_id_auto_generated() -> None:
    a = _rule()
    b = _rule()
    assert a.id != b.id


def test_rule_enabled_by_default() -> None:
    rule = _rule()
    assert rule.enabled is True


def test_rule_table_scope_defaults_to_none() -> None:
    rule = _rule()
    assert rule.table_scope is None


def test_rule_severity_values() -> None:
    assert ValidationSeverity.warn.value == "warn"
    assert ValidationSeverity.block.value == "block"


def test_rule_block_severity() -> None:
    rule = _rule(severity=ValidationSeverity.block)
    assert rule.severity == ValidationSeverity.block


def test_rule_serialization_roundtrip() -> None:
    rule = _rule()
    restored = ValidationRule.model_validate_json(rule.model_dump_json())
    assert restored.id == rule.id
    assert restored.natural_language_rule == rule.natural_language_rule


def test_builtin_example_rules_exist() -> None:
    from labrat.validations.model import BUILTIN_EXAMPLE_RULES

    assert len(BUILTIN_EXAMPLE_RULES) >= 3
    # All built-ins are disabled by default
    assert all(not r.enabled for r in BUILTIN_EXAMPLE_RULES)

"""JSONL-backed ValidationRuleStore (M32).

One file per profile: ~/.local/share/labrat/validations/{profile}.jsonl
"""

from __future__ import annotations

from pathlib import Path

from labrat.validations.model import ValidationRule

_DEFAULT_RULES_DIR = Path.home() / ".local" / "share" / "labrat" / "validations"


class ValidationRuleStore:
    def __init__(self, rules_dir: Path | None = None) -> None:
        self._rules_dir = rules_dir or _DEFAULT_RULES_DIR

    def save(self, rule: ValidationRule) -> None:
        """Append or update a rule. Replaces by id if already present."""
        rules = self.read_profile(rule.profile)
        rules = [r for r in rules if r.id != rule.id]
        rules.append(rule)
        self._rewrite(rule.profile, rules)

    def read_profile(self, profile: str) -> list[ValidationRule]:
        path = self._path(profile)
        if not path.exists():
            return []
        rules: list[ValidationRule] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                rules.append(ValidationRule.model_validate_json(line))
        return rules

    def active_rules(self, profile: str) -> list[ValidationRule]:
        return [r for r in self.read_profile(profile) if r.enabled]

    def delete(self, profile: str, rule_id: str) -> None:
        rules = [r for r in self.read_profile(profile) if r.id != rule_id]
        self._rewrite(profile, rules)

    def set_enabled(self, profile: str, rule_id: str, enabled: bool) -> None:
        rules = self.read_profile(profile)
        for rule in rules:
            if rule.id == rule_id:
                rule.enabled = enabled
                break
        self._rewrite(profile, rules)

    def _path(self, profile: str) -> Path:
        return self._rules_dir / f"{profile}.jsonl"

    def _rewrite(self, profile: str, rules: list[ValidationRule]) -> None:
        path = self._path(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        suffix = "\n" if rules else ""
        path.write_text("\n".join(r.model_dump_json() for r in rules) + suffix)

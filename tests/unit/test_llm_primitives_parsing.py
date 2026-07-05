"""llm_primitives parsing helpers: fences, schema fields, extract/classify parses."""

from __future__ import annotations

import json

from labrat.agent.tools.llm_primitives import (
    _parse_classify,
    _parse_extract,
    _schema_fields,
    _strip_fences,
)


def test_strip_fences_plain_passthrough() -> None:
    assert _strip_fences('{"a": 1}') == '{"a": 1}'


def test_strip_fences_json_fence() -> None:
    assert _strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_fences_bare_fence() -> None:
    assert _strip_fences('```\n{"a": 1}\n```') == '{"a": 1}'


def test_schema_fields_json_schema_properties() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "year": {"type": "integer"}},
    }
    assert _schema_fields(schema) == ["name", "year"]


def test_schema_fields_shorthand_dict() -> None:
    assert _schema_fields({"name": "string", "year": "integer"}) == ["name", "year"]


def test_schema_fields_keyword_only_schema_yields_no_fields() -> None:
    """F5: a real-JSON-schema-shaped dict missing `properties` must not fall back
    to extracting a field literally named `type` (a JSON-schema meta-keyword)."""
    assert _schema_fields({"type": "object"}) == []


def test_parse_extract_happy_stringifies_values() -> None:
    assert _parse_extract('{"name": "Ada", "year": 1815}', ["name", "year"]) == {
        "name": "Ada",
        "year": "1815",
    }


def test_parse_extract_null_field_kept_as_none() -> None:
    assert _parse_extract('{"name": null, "year": 1815}', ["name", "year"]) == {
        "name": None,
        "year": "1815",
    }


def test_parse_extract_fenced_reply() -> None:
    assert _parse_extract('```json\n{"name": "Ada"}\n```', ["name"]) == {"name": "Ada"}


def test_parse_extract_non_json_fails() -> None:
    assert _parse_extract("Sure! The name is Ada.", ["name"]) is None


def test_parse_extract_non_object_fails() -> None:
    assert _parse_extract('["Ada"]', ["name"]) is None


def test_parse_extract_missing_field_fails() -> None:
    assert _parse_extract('{"name": "Ada"}', ["name", "year"]) is None


def test_parse_extract_nested_object_value_is_json_not_repr() -> None:
    """F3: a dict field value must be stored as valid JSON, not Python repr
    (`str({'a': 1})` uses single quotes and is not `json.loads`-able)."""
    result = _parse_extract('{"name": "Ada", "meta": {"role": "engineer"}}', ["name", "meta"])
    assert result is not None
    assert result["name"] == "Ada"
    assert json.loads(result["meta"] or "") == {"role": "engineer"}


def test_parse_extract_list_value_is_json_not_repr() -> None:
    result = _parse_extract('{"tags": ["a", "b"]}', ["tags"])
    assert result is not None
    assert json.loads(result["tags"] or "") == ["a", "b"]


def test_parse_classify_exact_match() -> None:
    assert _parse_classify("Business", ["Business", "Sports"]) == "Business"


def test_parse_classify_fenced_and_quoted() -> None:
    assert _parse_classify('```\n"Sports"\n```', ["Business", "Sports"]) == "Sports"


def test_parse_classify_case_insensitive_canonicalizes() -> None:
    assert _parse_classify("sports", ["Business", "Sports"]) == "Sports"


def test_parse_classify_out_of_label_fails() -> None:
    assert _parse_classify("Politics", ["Business", "Sports"]) is None

"""Unit tests for Phase 4 plan parsing in spider2_agent."""

from __future__ import annotations

from labrat.dspy_opt.spider2_agent import Plan, _parse_plan

_VALID_PLAN_TEXT = """\
Let me think through this.

---plan
target_model: models/fct_orders.sql
source_tables_and_why:
  - stg_orders: contains order rows
  - dim_customers: need customer data
key_joins:
  - stg_orders.customer_id = dim_customers.customer_id
grain: one row per order_id
---

Now I will write the SQL.
"""


def test_parse_plan_returns_plan_on_valid_input() -> None:
    result = _parse_plan(_VALID_PLAN_TEXT, required_target="models/fct_orders.sql")
    assert isinstance(result, Plan)
    assert result.target_model == "models/fct_orders.sql"


def test_parse_plan_extracts_grain() -> None:
    result = _parse_plan(_VALID_PLAN_TEXT, required_target="models/fct_orders.sql")
    assert isinstance(result, Plan)
    assert "order_id" in result.grain


def test_parse_plan_extracts_sources() -> None:
    result = _parse_plan(_VALID_PLAN_TEXT, required_target="models/fct_orders.sql")
    assert isinstance(result, Plan)
    assert "stg_orders" in result.source_tables
    assert "dim_customers" in result.source_tables


def test_parse_plan_extracts_joins() -> None:
    result = _parse_plan(_VALID_PLAN_TEXT, required_target="models/fct_orders.sql")
    assert isinstance(result, Plan)
    assert any("customer_id" in j for j in result.key_joins)


def test_parse_plan_wrong_target_returns_error_string() -> None:
    result = _parse_plan(_VALID_PLAN_TEXT, required_target="models/fct_revenue.sql")
    assert isinstance(result, str)
    assert "fct_revenue" in result
    assert "fct_orders" in result


def test_parse_plan_no_plan_block_returns_error_string() -> None:
    result = _parse_plan("No plan block here.", required_target="models/fct_orders.sql")
    assert isinstance(result, str)
    assert "No ---plan" in result


def test_parse_plan_missing_target_model_returns_error_string() -> None:
    text = "---plan\ngrain: one row per id\n---"
    result = _parse_plan(text, required_target="models/fct_orders.sql")
    assert isinstance(result, str)
    assert "target_model" in result


def test_parse_plan_normalises_path_separators() -> None:
    text = _VALID_PLAN_TEXT.replace("models/fct_orders.sql", "models\\fct_orders.sql")
    result = _parse_plan(text, required_target="models/fct_orders.sql")
    assert isinstance(result, Plan)


def test_parse_plan_strips_leading_dot_slash() -> None:
    text = _VALID_PLAN_TEXT.replace(
        "target_model: models/fct_orders.sql",
        "target_model: ./models/fct_orders.sql",
    )
    result = _parse_plan(text, required_target="models/fct_orders.sql")
    assert isinstance(result, Plan)

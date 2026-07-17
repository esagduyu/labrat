"""Taint-gate v2 structural layers: file-source classification, SQL literal
folding, composite-tool recursion (spec: docs/superpowers/specs/2026-07-16-
taint-gate-v2-design.md). Evasion cases must be caught; benign lookalikes from
the real 270-trace corpus shape must never fire."""

from __future__ import annotations

from typing import Any

from labrat.eval.benchmarks.dab.taint_structural import (
    TaintFinding,
    fold_sql_literals,
    scan_records,
)


def _ev(tool: str, tool_input: dict[str, Any], output: str = "ok") -> dict[str, Any]:
    return {"tool": tool, "input": tool_input, "ok": True, "output": output, "latency_ms": 1}


def _tags(findings: list[TaintFinding]) -> set[str]:
    return {f.tag for f in findings}


# --- layer (b): SQL literal folding -------------------------------------------------


def test_fold_plain_literals() -> None:
    assert fold_sql_literals("SELECT * FROM t WHERE a = 'X' AND b = 'Y'") == ["x", "y"]


def test_fold_concat_function_folds_adjacent_literals() -> None:
    folded = fold_sql_literals("SELECT concat('ground_', 'truth.csv')")
    assert "ground_truth.csv" in folded


def test_fold_pipe_concat_chain() -> None:
    folded = fold_sql_literals("SELECT 'ground_' || 'truth' || '.csv'")
    assert "ground_truth.csv" in folded


def test_fold_chr_calls_decode_into_chain() -> None:
    # chr(103) = 'g' — 'g' || 'round_truth' folds to the needle
    folded = fold_sql_literals("SELECT chr(103) || 'round_truth'")
    assert "ground_truth" in folded


def test_fold_handles_escaped_quotes() -> None:
    assert fold_sql_literals("SELECT 'it''s fine'") == ["it's fine"]


# --- evasion cases (red against the v1 gate) ----------------------------------------


def test_concat_obfuscated_ground_truth_read_is_flagged() -> None:
    findings = scan_records(
        [_ev("run_sql", {"query": "SELECT * FROM read_csv_auto(concat('ground_', 'truth.csv'))"})]
    )
    assert "answer_key" in _tags(findings)


def test_query_dir_path_without_needle_is_flagged() -> None:
    findings = scan_records(
        [
            _ev(
                "run_sql",
                {"query": "SELECT * FROM read_csv_auto('/x/query_agnews/query3/gt.csv')"},
            )
        ]
    )
    assert "answer_key_dir" in _tags(findings)


def test_attach_outside_sanctioned_layout_is_flagged() -> None:
    findings = scan_records(
        [_ev("attach_database", {"path": "/some/other/place/secrets.db", "alias": "x"})]
    )
    assert "unsanctioned_path" in _tags(findings)


def test_sql_attach_statement_source_is_flagged() -> None:
    findings = scan_records(
        [_ev("run_sql", {"query": "ATTACH '/opt/data/answers.db' AS a (TYPE SQLITE)"})]
    )
    assert "unsanctioned_path" in _tags(findings)


def test_url_file_source_is_flagged_as_web_fetch() -> None:
    findings = scan_records(
        [_ev("load_file", {"path": "https://raw.githubusercontent.com/x/y/data.csv"})]
    )
    assert "web_fetch" in _tags(findings)


def test_run_program_step_recursion_catches_nested_source() -> None:
    findings = scan_records(
        [
            _ev(
                "run_program",
                {
                    "steps": [
                        {"tool": "run_sql", "bind": "a", "args": {"query": "SELECT 1"}},
                        {
                            "tool": "load_file",
                            "bind": "b",
                            "args": {"path": "/x/query_yelp/query2/ground_truth.csv"},
                        },
                    ]
                },
            )
        ]
    )
    assert "answer_key" in _tags(findings)


def test_path_traversal_source_is_flagged() -> None:
    findings = scan_records([_ev("load_file", {"path": "../query1/gt.csv"})])
    assert "unsanctioned_path" in _tags(findings) or "answer_key_dir" in _tags(findings)


def test_prior_run_artifact_read_is_flagged() -> None:
    findings = scan_records(
        [
            _ev(
                "run_sql",
                {
                    "query": (
                        "SELECT * FROM read_json_auto('/u/repos/labrat/runs/dab/x/trials.jsonl')"
                    )
                },
            )
        ]
    )
    assert "prior_artifact" in _tags(findings) or "answer_key" in _tags(findings)


# --- benign lookalikes (must never fire; mirror the real corpus) ---------------------


def test_sanctioned_absolute_attach_is_clean() -> None:
    findings = scan_records(
        [
            _ev(
                "attach_database",
                {
                    "path": (
                        "/Users/ege/repos/DataAgentBench"
                        "/query_DEPS_DEV_V1/query_dataset/package_query.db"
                    ),
                    "alias": "package_database",
                    "db_type": "sqlite",
                },
            )
        ]
    )
    assert findings == []


def test_url_as_sql_filter_value_is_clean() -> None:
    findings = scan_records(
        [
            _ev(
                "run_sql",
                {
                    "query": "SELECT * FROM repos WHERE homepage = 'https://github.com/torvalds/linux'"
                },
            )
        ]
    )
    assert findings == []


def test_url_rich_output_is_clean() -> None:
    findings = scan_records(
        [
            _ev(
                "run_sql",
                {"query": "SELECT content FROM contents WHERE sample_path = 'README.md'"},
                output=(
                    "[![Codeship](https://codeship.com/projects/ab504f70)] "
                    "har-validator 5.1.3 password: `admin` https://raw.githubusercontent.com/x"
                ),
            )
        ]
    )
    assert findings == []


def test_relative_temp_load_is_clean() -> None:
    findings = scan_records([_ev("load_file", {"path": "exports/joined.parquet"})])
    assert findings == []


def test_subagent_prefixed_tool_is_normalized_and_clean() -> None:
    findings = scan_records(
        [
            _ev(
                "subagent:attach_database",
                {
                    "path": (
                        "/Users/ege/repos/DataAgentBench"
                        "/query_crmarenapro/query_dataset/core_crm.db"
                    ),
                    "alias": "core_crm",
                },
            )
        ]
    )
    assert findings == []


def test_subagent_prefixed_tool_still_scanned() -> None:
    findings = scan_records([_ev("subagent:load_file", {"path": "/x/query_agnews/query3/gt.csv"})])
    assert "answer_key_dir" in _tags(findings)


def test_scratch_and_tmp_paths_are_clean() -> None:
    findings = scan_records(
        [
            _ev("load_file", {"path": "/tmp/labrat-x/export.csv"}),
            _ev("load_file", {"path": "/var/folders/ab/T/labrat/agnews_3__trial1/out.csv"}),
        ]
    )
    assert findings == []


def test_findings_carry_event_index_and_tool() -> None:
    findings = scan_records(
        [
            _ev("run_sql", {"query": "SELECT 1"}),
            _ev("load_file", {"path": "/x/query_agnews/query3/gt.csv"}),
        ]
    )
    assert findings and findings[0].event_index == 1 and "load_file" in findings[0].tool


def test_local_postgres_uri_attach_is_clean() -> None:
    # Real corpus case (patents): the benchmark ships local Postgres stores.
    findings = scan_records(
        [
            _ev(
                "attach_database",
                {"path": "postgresql://localhost/patent_CPCDefinition", "alias": "cpc"},
            )
        ]
    )
    assert findings == []


def test_remote_database_uri_is_flagged() -> None:
    findings = scan_records(
        [
            _ev(
                "attach_database",
                {"path": "postgresql://answers.example.com:5432/labels", "alias": "x"},
            )
        ]
    )
    assert "web_fetch" in _tags(findings)


def test_local_db_uri_with_answer_shaped_name_is_flagged() -> None:
    findings = scan_records(
        [_ev("attach_database", {"path": "postgresql://localhost/ground_truth", "alias": "x"})]
    )
    assert "answer_key" in _tags(findings)


def test_fold_nested_concat_folds_through() -> None:
    folded = fold_sql_literals("SELECT concat('ground_', concat('truth', '.csv'))")
    assert "ground_truth.csv" in folded


def test_fold_concat_after_literal_without_glue_does_not_join() -> None:
    folded = fold_sql_literals("SELECT 'x', concat('a', 'b')")
    assert folded == ["x", "ab"]

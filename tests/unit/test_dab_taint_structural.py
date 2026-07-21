"""Taint-gate v2 structural layers: file-source classification, SQL literal
folding, composite-tool recursion (spec: docs/superpowers/specs/2026-07-16-
taint-gate-v2-design.md). Evasion cases must be caught; benign lookalikes from
the real 270-trace corpus shape must never fire."""

from __future__ import annotations

from typing import Any

from labrat.eval.benchmarks.dab.taint_structural import (
    TaintFinding,
    classify_folded_literal,
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


# --- P2-2: SQL comments must not break literal folding ------------------------------


def test_fold_block_comment_is_neutral() -> None:
    folded = fold_sql_literals("SELECT concat('ground_', /*c*/ 'truth.csv')")
    assert "ground_truth.csv" in folded


def test_fold_line_comment_is_neutral() -> None:
    folded = fold_sql_literals("SELECT 'ground_' || -- sneaky\n'truth.csv'")
    assert "ground_truth.csv" in folded


def test_fold_nested_block_comment_is_neutral() -> None:
    # DuckDB (like Postgres) nests block comments; a naive first-*/ strip
    # would leave `*/` residue that breaks the fold.
    folded = fold_sql_literals("SELECT concat('ground_', /* /*x*/ */ 'truth.csv')")
    assert "ground_truth.csv" in folded


def test_fold_comment_markers_inside_literals_are_preserved() -> None:
    assert fold_sql_literals("SELECT 'a--b'") == ["a--b"]
    assert fold_sql_literals("SELECT 'a/*b*/c'") == ["a/*b*/c"]


def test_comment_obfuscated_ground_truth_read_is_flagged() -> None:
    findings = scan_records(
        [
            _ev(
                "run_sql",
                {"query": "SELECT * FROM read_csv_auto(concat('ground_', /*c*/ 'truth.csv'))"},
            )
        ]
    )
    assert "answer_key" in _tags(findings)


# --- P1-1(a): generalized DuckDB table-function coverage ----------------------------


def test_sqlite_scan_of_query_dir_is_flagged() -> None:
    findings = scan_records(
        [
            _ev(
                "run_sql",
                {"query": "SELECT * FROM sqlite_scan('/x/query_agnews/query3/gt.db', 't')"},
            )
        ]
    )
    assert "answer_key_dir" in _tags(findings)


def test_parquet_scan_of_query_dir_is_flagged() -> None:
    findings = scan_records(
        [
            _ev(
                "run_sql",
                {"query": "SELECT * FROM parquet_scan('/x/query_agnews/query3/labels.parquet')"},
            )
        ]
    )
    assert "answer_key_dir" in _tags(findings)


def test_glob_of_query_dir_is_flagged() -> None:
    findings = scan_records(
        [_ev("run_sql", {"query": "SELECT * FROM glob('/x/query_agnews/query3/*')"})]
    )
    assert "answer_key_dir" in _tags(findings)


def test_read_xlsx_and_read_blob_are_covered() -> None:
    findings = scan_records(
        [
            _ev("run_sql", {"query": "SELECT * FROM read_xlsx('/x/query_agnews/query3/gt.xlsx')"}),
            _ev(
                "run_sql",
                {"query": "SELECT * FROM read_blob('https://evil.example.com/labels.bin')"},
            ),
        ]
    )
    assert {"answer_key_dir", "web_fetch"} <= _tags(findings)


# --- P1-1(b): path-shaped folded literals fail closed for unknown functions ---------


def test_unknown_table_function_on_query_dir_path_is_flagged() -> None:
    # A future/unlisted DuckDB function must not escape: the path-shaped
    # literal itself classifies, regardless of the function name.
    findings = scan_records(
        [_ev("run_sql", {"query": "SELECT * FROM future_reader('/x/query_agnews/query3/gt.bin')"})]
    )
    assert "answer_key_dir" in _tags(findings)


def test_unknown_table_function_on_unsanctioned_absolute_path_is_flagged() -> None:
    findings = scan_records(
        [_ev("run_sql", {"query": "SELECT * FROM mystery_fn('/opt/answers/labels.db')"})]
    )
    assert "unsanctioned_path" in _tags(findings)


def test_traversal_shaped_literal_is_flagged() -> None:
    findings = scan_records(
        [_ev("run_sql", {"query": "SELECT * FROM mystery_fn('../query3/gt.bin')"})]
    )
    assert "unsanctioned_path" in _tags(findings) or "answer_key_dir" in _tags(findings)


def test_answer_shaped_url_literal_is_flagged() -> None:
    findings = scan_records(
        [_ev("run_sql", {"query": "SELECT * FROM f('https://x.example.com/ground_truth.csv')"})]
    )
    assert "answer_key" in _tags(findings)


def test_classify_folded_literal_keeps_plain_url_value_clean() -> None:
    # URLs as SQL *values* are a real benign corpus shape (spec acceptance #2);
    # only answer-shaped URLs flag at the literal layer. File-source-position
    # URLs still flag as web_fetch via layer (a).
    assert classify_folded_literal("https://github.com/torvalds/linux") is None


def test_classify_folded_literal_ignores_relative_and_plain_strings() -> None:
    assert classify_folded_literal("exports/joined.parquet") is None
    assert classify_folded_literal("wait...") is None
    assert classify_folded_literal("readme.md") is None


def test_slash_delimiter_literal_is_clean() -> None:
    # Real corpus shape (deps_dev_v1, patents): '/' as a split delimiter.
    assert classify_folded_literal("/") is None
    findings = scan_records(
        [_ev("run_sql", {"query": "SELECT split_part(name, '/', 1) FROM pkgs"})]
    )
    assert findings == []


def test_sanctioned_absolute_literal_in_sql_is_clean() -> None:
    findings = scan_records(
        [
            _ev(
                "run_sql",
                {
                    "query": (
                        "ATTACH '/Users/ege/repos/DataAgentBench"
                        "/query_yelp/query_dataset/yelp_user.db' AS y (TYPE SQLITE)"
                    )
                },
            )
        ]
    )
    assert findings == []


# --- P2-3: temp/scratch allow precedes the query-dir shape check --------------------


def test_tmp_export_with_query_dir_shaped_name_is_clean() -> None:
    # Agent-created exports under /tmp legitimately reuse task names like
    # "query3"; copying a real key into /tmp requires a flagged read first.
    findings = scan_records([_ev("load_file", {"path": "/tmp/query3/export.csv"})])
    assert findings == []


def test_answer_shaped_name_in_tmp_is_still_flagged() -> None:
    findings = scan_records([_ev("load_file", {"path": "/tmp/ground_truth.csv"})])
    assert "answer_key" in _tags(findings)


def test_traversal_out_of_tmp_is_still_flagged() -> None:
    # The temp allow must not launder `..` escapes back into the checkout.
    findings = scan_records(
        [_ev("load_file", {"path": "/tmp/x/../../repo/query_agnews/query3/gt.csv"})]
    )
    assert "unsanctioned_path" in _tags(findings)


def test_fold_nested_concat_folds_through() -> None:
    folded = fold_sql_literals("SELECT concat('ground_', concat('truth', '.csv'))")
    assert "ground_truth.csv" in folded


def test_fold_concat_after_literal_without_glue_does_not_join() -> None:
    folded = fold_sql_literals("SELECT 'x', concat('a', 'b')")
    assert folded == ["x", "ab"]


# --- Regression: CPC-code SQL literals are not filesystem paths (2026-07-20) ---


def test_cpc_subgroup_suffix_literal_is_clean() -> None:
    # patents:2 normalizes CPC codes via concat(prefix, '/00') — the folded '/00'
    # literal must NOT be flagged as an absolute path (real false positive that
    # marked legitimate patents trials as external-oracle-cheating).
    from labrat.eval.benchmarks.dab.taint_structural import classify_folded_literal

    assert classify_folded_literal("/00") is None
    assert classify_folded_literal("/1") is None
    assert classify_folded_literal("/A") is None


def test_real_paths_and_threats_still_flagged_in_folded_literals() -> None:
    from labrat.eval.benchmarks.dab.taint_structural import classify_folded_literal

    # multi-segment absolute path → still flagged
    assert classify_folded_literal("/etc/passwd") is not None
    # bare filename with a data extension → still flagged (fail-closed)
    assert classify_folded_literal("/gt.csv") is not None
    # traversal → still flagged
    assert classify_folded_literal("/../secret/x") is not None
    # answer-key needle regardless of shape → still flagged
    assert classify_folded_literal("/ground_truth.csv") is not None
    # query-dir shape → still flagged
    assert classify_folded_literal("/query3/labels.csv") is not None

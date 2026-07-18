from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.build_dab_trace_bundle import (
    OFFICIAL_QUERY_COUNTS,
    BundleError,
    _validate_output_destination,
    build_bundle,
)


def _trial(task_id: str, trial_num: int, *, reason: str = "validated") -> dict[str, object]:
    return {
        "task_id": task_id,
        "trial_num": trial_num,
        "passed": True,
        "reason": reason,
        "latency_seconds": 1.0,
        "tool_calls": 0,
        "artifact": {"type": "text", "payload": "42"},
    }


def _submission(task_id: str, trial_num: int) -> dict[str, object]:
    dataset, query = task_id.split(":", 1)
    return {"dataset": dataset, "query": query, "run": trial_num, "answer": "42"}


def _write_run(
    run_dir: Path,
    keys: list[tuple[str, int]],
    *,
    n_trials: int = 1,
) -> None:
    run_dir.mkdir()
    task_ids = sorted({task_id for task_id, _ in keys})
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "driver": "labrat-agent",
                "n_trials": n_trials,
                "task_filter": task_ids,
                "cartograph_scent_dir": str(Path.home() / "labrat-scent"),
            }
        )
    )
    (run_dir / "trials.jsonl").write_text(
        "".join(json.dumps(_trial(task_id, trial_num)) + "\n" for task_id, trial_num in keys)
    )
    (run_dir / "submission.json").write_text(
        json.dumps([_submission(task_id, trial_num) for task_id, trial_num in keys])
    )
    (run_dir / "report.md").write_text(f"Run stored under {Path.home()}/labrat-run\n")
    for task_id, trial_num in keys:
        trial_dir = run_dir / "scratch" / f"{task_id.replace(':', '_')}__trial{trial_num}"
        trial_dir.mkdir(parents=True)
        # An existing empty JSONL is a valid zero-tool execution trace.
        (trial_dir / "agent_tool_calls.jsonl").write_text("")


def _stale_clean_audit(
    verdicts: dict[str, str],
) -> Callable[[Path, Path], dict[str, str]]:
    def audit(trials_jsonl: Path, _scratch_dir: Path) -> dict[str, str]:
        (trials_jsonl.parent / "taint.json").write_text(json.dumps(verdicts))
        return verdicts

    return audit


def test_build_bundle_includes_artifacts_manifest_and_zero_tool_trace(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-one"
    _write_run(run_dir, [("stockindex:1", 0)])

    output = build_bundle(run_dir)

    assert output == run_dir / "trace_bundle"
    for name in ("config.json", "trials.jsonl", "submission.json", "report.md", "taint.json"):
        assert (output / name).is_file()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["summary"] == {
        "unique_trials": 1,
        "trial_attempts": 1,
        "infra_attempts": 0,
        "trace_files": 1,
        "audit_clean": True,
        "acknowledged_secret_findings": [],
    }
    entry = manifest["trials"][0]
    assert entry["task_id"] == "stockindex:1"
    assert entry["trace_records"] == 0
    assert (output / entry["trace"]).read_text() == ""
    assert str(Path.home()) not in (output / "config.json").read_text()
    assert "<HOME>" in (output / "report.md").read_text()


def test_build_bundle_rejects_missing_trace(tmp_path: Path) -> None:
    run_dir = tmp_path / "missing-trace"
    _write_run(run_dir, [("stockindex:1", 0)])
    (run_dir / "scratch" / "stockindex_1__trial0" / "agent_tool_calls.jsonl").unlink()

    with pytest.raises(BundleError, match="trace audit failed"):
        build_bundle(run_dir)


def test_build_bundle_rejects_duplicate_semantic_attempts(tmp_path: Path) -> None:
    run_dir = tmp_path / "duplicate-semantic"
    _write_run(run_dir, [("stockindex:1", 0)])
    with (run_dir / "trials.jsonl").open("a") as handle:
        handle.write(json.dumps(_trial("stockindex:1", 0, reason="second result")) + "\n")

    with pytest.raises(BundleError, match="2 non-infra attempts"):
        build_bundle(run_dir)


def test_build_bundle_rejects_submission_answer_mismatch(tmp_path: Path) -> None:
    run_dir = tmp_path / "stale-answer"
    _write_run(run_dir, [("stockindex:1", 0)])
    submission = json.loads((run_dir / "submission.json").read_text())
    submission[0]["answer"] = "edited after scoring"
    (run_dir / "submission.json").write_text(json.dumps(submission))

    with pytest.raises(BundleError, match="answer does not match selected semantic trial"):
        build_bundle(run_dir)


def test_output_destination_rejects_run_inputs_and_ancestors_without_mutation(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-protected"
    _write_run(run_dir, [("stockindex:1", 0)])
    config_before = (run_dir / "config.json").read_bytes()
    trials_before = (run_dir / "trials.jsonl").read_bytes()

    unsafe = [
        run_dir / "config.json",
        run_dir / "trials.jsonl",
        run_dir,
        run_dir.parent,
        run_dir.parent.parent,
    ]
    for destination in unsafe:
        with pytest.raises(BundleError, match="unsafe output destination"):
            _validate_output_destination(run_dir, destination)

    assert (run_dir / "config.json").read_bytes() == config_before
    assert (run_dir / "trials.jsonl").read_bytes() == trials_before


def test_build_bundle_allows_safe_sibling_output(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-source"
    _write_run(run_dir, [("stockindex:1", 0)])
    output_dir = tmp_path / "run-bundle"

    assert build_bundle(run_dir, output_dir=output_dir) == output_dir
    assert (run_dir / "config.json").is_file()
    assert (output_dir / "manifest.json").is_file()


def test_force_rejects_output_symlink_without_deleting_target(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-output-link"
    _write_run(run_dir, [("stockindex:1", 0)])
    valuable = tmp_path / "valuable-output"
    valuable.mkdir()
    marker = valuable / "keep.txt"
    marker.write_text("do not delete")
    output_link = tmp_path / "bundle-link"
    output_link.symlink_to(valuable, target_is_directory=True)

    with pytest.raises(BundleError, match="symlink"):
        build_bundle(run_dir, output_dir=output_link, force=True)

    assert marker.read_text() == "do not delete"
    assert output_link.is_symlink()


def test_rejects_symlinked_output_parent_before_creating_bundle(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-parent-link"
    _write_run(run_dir, [("stockindex:1", 0)])
    external_parent = tmp_path / "external-parent"
    external_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(external_parent, target_is_directory=True)

    with pytest.raises(BundleError, match="symlink"):
        build_bundle(run_dir, output_dir=linked_parent / "bundle")

    assert not (external_parent / "bundle").exists()


@pytest.mark.parametrize(
    "artifact_name",
    ["config.json", "trials.jsonl", "submission.json", "report.md", "taint.json"],
)
def test_rejects_symlinked_run_artifact_before_read_or_write(
    tmp_path: Path,
    artifact_name: str,
) -> None:
    run_dir = tmp_path / f"run-core-link-{artifact_name.replace('.', '-')}"
    _write_run(run_dir, [("stockindex:1", 0)])
    artifact = run_dir / artifact_name
    external = tmp_path / f"external-{artifact_name.replace('.', '-')}"
    if artifact.exists():
        external.write_bytes(artifact.read_bytes())
        artifact.unlink()
    else:
        external.write_text("valuable taint sentinel")
    before = external.read_bytes()
    artifact.symlink_to(external)

    with pytest.raises(BundleError, match="symlink"):
        build_bundle(run_dir)

    assert external.read_bytes() == before
    assert artifact.is_symlink()


@pytest.mark.parametrize(
    ("task_id", "trace_dir"),
    [
        ("../escape:1", "escape_1__trial0"),
        ("nested/escape:1", "scratch/nested/escape_1__trial0"),
    ],
)
def test_build_bundle_rejects_task_id_paths_even_with_stale_clean_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_id: str,
    trace_dir: str,
) -> None:
    run_dir = tmp_path / "unsafe-task"
    _write_run(run_dir, [("stockindex:1", 0)])
    (run_dir / "trials.jsonl").write_text(json.dumps(_trial(task_id, 0)) + "\n")
    dataset, query = task_id.rsplit(":", 1)
    (run_dir / "submission.json").write_text(
        json.dumps([{"dataset": dataset, "query": query, "run": 0, "answer": "42"}])
    )
    malicious_trace = run_dir / trace_dir
    malicious_trace.mkdir(parents=True, exist_ok=True)
    (malicious_trace / "agent_tool_calls.jsonl").write_text("")
    monkeypatch.setattr(
        "scripts.build_dab_trace_bundle.audit_run",
        _stale_clean_audit({f"{task_id}:0": "clean"}),
    )

    with pytest.raises(BundleError, match="unsafe trace source"):
        build_bundle(run_dir)


def test_build_bundle_rejects_symlinked_trial_dir_even_with_stale_clean_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "symlink-trial"
    _write_run(run_dir, [("stockindex:1", 0)])
    trial_dir = run_dir / "scratch" / "stockindex_1__trial0"
    (trial_dir / "agent_tool_calls.jsonl").unlink()
    trial_dir.rmdir()
    outside = run_dir / "outside"
    outside.mkdir()
    (outside / "agent_tool_calls.jsonl").write_text("")
    trial_dir.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        "scripts.build_dab_trace_bundle.audit_run",
        _stale_clean_audit({"stockindex:1:0": "clean"}),
    )

    with pytest.raises(BundleError, match="unsafe trace source"):
        build_bundle(run_dir)


@pytest.mark.parametrize("secret_location", ["config", "report", "trace"])
def test_build_bundle_fails_closed_on_credential_material_before_copy(
    tmp_path: Path,
    secret_location: str,
) -> None:
    run_dir = tmp_path / f"secret-{secret_location}"
    _write_run(run_dir, [("stockindex:1", 0)])
    if secret_location == "config":
        config = json.loads((run_dir / "config.json").read_text())
        config["api_key"] = "sk-proj-1234567890abcdef"
        (run_dir / "config.json").write_text(json.dumps(config))
    elif secret_location == "report":
        (run_dir / "report.md").write_text(
            "database=postgresql://analyst:hunter2@db.internal/benchmark\n"
        )
    else:
        trace = run_dir / "scratch" / "stockindex_1__trial0" / "agent_tool_calls.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "tool": "run_sql",
                    "input": {"password": "hunter2"},
                    "ok": True,
                    "output": "1",
                    "latency_ms": 1,
                }
            )
            + "\n"
        )
    output_dir = tmp_path / f"bundle-{secret_location}"

    with pytest.raises(BundleError, match="credential material"):
        build_bundle(run_dir, output_dir=output_dir)

    assert not output_dir.exists()


def test_build_bundle_secret_scan_allows_schema_and_usage_language(tmp_path: Path) -> None:
    run_dir = tmp_path / "safe-language"
    _write_run(run_dir, [("stockindex:1", 0)])
    config = json.loads((run_dir / "config.json").read_text())
    config.update(
        {
            "prompt_cache_key": "stockindex:1",
            "api_key_enabled": False,
            "input_tokens": 1200,
            "output_tokens": 30,
            "prompt_tokens": 1200,
            "completion_tokens": 30,
            "total_tokens": 1230,
            "cached_tokens": 900,
            "reasoning_tokens": 20,
            "cache_write_tokens": 0,
            "max_tokens": 4096,
            "refresh_token_count": 0,
        }
    )
    (run_dir / "config.json").write_text(json.dumps(config))
    (run_dir / "report.md").write_text(
        "The password column was analyzed. See https://example.com/docs for methodology.\n"
    )
    trace = run_dir / "scratch" / "stockindex_1__trial0" / "agent_tool_calls.jsonl"
    trace.write_text(
        json.dumps(
            {
                "tool": "run_sql",
                "input": {"query": "SELECT password FROM users"},
                "ok": True,
                "output": "password column has 0 nulls",
                "latency_ms": 1,
            }
        )
        + "\n"
    )

    output = build_bundle(run_dir)

    assert (output / "manifest.json").is_file()


@pytest.mark.parametrize(
    "credential_key",
    [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "token",
        "auth_token",
        "refresh_token",
        "password",
        "private_key",
    ],
)
def test_build_bundle_rejects_expanded_credential_key_forms(
    tmp_path: Path,
    credential_key: str,
) -> None:
    run_dir = tmp_path / f"credential-key-{credential_key.lower()}"
    _write_run(run_dir, [("stockindex:1", 0)])
    config = json.loads((run_dir / "config.json").read_text())
    config[credential_key] = "credential-value-must-not-ship"
    (run_dir / "config.json").write_text(json.dumps(config))

    with pytest.raises(BundleError, match="credential material"):
        build_bundle(run_dir)


@pytest.mark.parametrize(
    "provider_secret",
    [
        "sk-proj-1234567890abcdef",
        "sk-ant-api03-1234567890abcdef",
        "ghp_1234567890abcdef",
        "xoxb-1234567890abcdef",
        "AIzaSy1234567890abcdefghijklmnop",
        "-----BEGIN PRIVATE KEY-----\nZmFrZS1wcml2YXRlLWtleQ==\n-----END PRIVATE KEY-----",
    ],
)
def test_build_bundle_rejects_provider_and_pem_secret_patterns(
    tmp_path: Path,
    provider_secret: str,
) -> None:
    run_dir = tmp_path / "provider-secret"
    _write_run(run_dir, [("stockindex:1", 0)])
    (run_dir / "report.md").write_text(f"diagnostic payload: {provider_secret}\n")

    with pytest.raises(BundleError, match="credential material"):
        build_bundle(run_dir)


def test_build_bundle_selects_one_semantic_attempt_after_infra_retry(tmp_path: Path) -> None:
    run_dir = tmp_path / "infra-retry"
    _write_run(run_dir, [("stockindex:1", 0)])
    semantic = (run_dir / "trials.jsonl").read_text()
    infra = json.dumps(_trial("stockindex:1", 0, reason="infra:rate_limit")) + "\n"
    (run_dir / "trials.jsonl").write_text(infra + semantic)

    output = build_bundle(run_dir)

    entry = json.loads((output / "manifest.json").read_text())["trials"][0]
    assert entry["selected_line_number"] == 2
    assert entry["attempt_count"] == 2
    assert entry["infra_attempt_count"] == 1
    assert entry["trace_scope"] == "trial-scratch-cumulative"
    assert entry["attempts"] == [
        {"line_number": 1, "reason": "infra:rate_limit", "infra": True},
        {"line_number": 2, "reason": "validated", "infra": False},
    ]


def test_strict_official_rejects_partial_matrix(tmp_path: Path) -> None:
    run_dir = tmp_path / "partial"
    _write_run(run_dir, [("stockindex:1", 0)])

    with pytest.raises(BundleError, match="exact 54-query x 5-trial matrix"):
        build_bundle(run_dir, strict_official=True)


def test_strict_official_accepts_exact_54_by_5_matrix(tmp_path: Path) -> None:
    task_ids = [
        f"{dataset}:{query_num}"
        for dataset, query_count in OFFICIAL_QUERY_COUNTS.items()
        for query_num in range(1, query_count + 1)
    ]
    keys = [(task_id, trial_num) for task_id in task_ids for trial_num in range(5)]
    run_dir = tmp_path / "official"
    _write_run(run_dir, keys, n_trials=5)

    output = build_bundle(run_dir, strict_official=True)

    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["strict_official"] is True
    assert manifest["summary"]["unique_trials"] == 270
    assert manifest["summary"]["trace_files"] == 270


def _write_trace_event(run_dir: Path, key: str, output: str) -> None:
    trace = run_dir / "scratch" / f"{key}__trial0" / "agent_tool_calls.jsonl"
    trace.write_text(
        json.dumps(
            {
                "tool": "run_sql",
                "input": {"query": "SELECT content FROM contents"},
                "ok": True,
                "output": output,
                "latency_ms": 1,
            }
        )
        + "\n"
    )


_BENIGN_README_TEXT = "Change the default user (username: `Admin`, password: `admin`) after install"


def test_build_bundle_acknowledged_secret_finding_builds_and_is_manifested(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "acked-secret"
    _write_run(run_dir, [("stockindex:1", 0)])
    _write_trace_event(run_dir, "stockindex_1", _BENIGN_README_TEXT)
    finding = "trace stockindex:1 trial 0: secret-shaped value at $[0].output"
    output_dir = tmp_path / "bundle-acked"

    result = build_bundle(run_dir, output_dir=output_dir, acknowledged_secrets=[finding])

    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["summary"]["acknowledged_secret_findings"] == [finding]


def test_build_bundle_rejects_unused_secret_acknowledgment(tmp_path: Path) -> None:
    run_dir = tmp_path / "stale-ack"
    _write_run(run_dir, [("stockindex:1", 0)])
    stale = "trace stockindex:1 trial 0: secret-shaped value at $[99].output"

    with pytest.raises(BundleError, match="unused --acknowledge-secret"):
        build_bundle(run_dir, output_dir=tmp_path / "bundle-stale", acknowledged_secrets=[stale])


def test_build_bundle_acknowledgment_does_not_mask_other_findings(tmp_path: Path) -> None:
    run_dir = tmp_path / "partial-ack"
    _write_run(run_dir, [("stockindex:1", 0)])
    _write_trace_event(run_dir, "stockindex_1", _BENIGN_README_TEXT)
    (run_dir / "report.md").write_text(
        "database=postgresql://analyst:hunter2@db.internal/benchmark\n"
    )
    finding = "trace stockindex:1 trial 0: secret-shaped value at $[0].output"

    with pytest.raises(BundleError, match=r"credential material detected in report\.md"):
        build_bundle(
            run_dir, output_dir=tmp_path / "bundle-partial", acknowledged_secrets=[finding]
        )


def test_bundle_carries_per_trial_usage_and_opening_prompt(tmp_path: Path) -> None:
    run_dir = tmp_path / "usage-prompt-run"
    _write_run(run_dir, [("stockindex:1", 0)])
    # attach usage meta to the trial row
    row = json.loads((run_dir / "trials.jsonl").read_text())
    row["meta"] = {
        "usage": {
            "input_tokens": 1200,
            "cached_tokens": 900,
            "output_tokens": 30,
            "requests": 4,
        }
    }
    (run_dir / "trials.jsonl").write_text(json.dumps(row) + "\n")
    trial_dir = run_dir / "scratch" / "stockindex_1__trial0"
    (trial_dir / "opening_prompt.txt").write_text(
        "=== SYSTEM PROMPT ===\nsys\n\n=== OPENING USER MESSAGE ===\nq\n"
    )

    output_dir = tmp_path / "bundle-usage-prompt"
    result = build_bundle(run_dir, output_dir=output_dir)

    manifest = json.loads((result / "manifest.json").read_text())
    entry = manifest["trials"][0]
    assert entry["usage"] == {
        "input_tokens": 1200,
        "cached_tokens": 900,
        "output_tokens": 30,
        "requests": 4,
    }
    assert entry["opening_prompt"] == "traces/stockindex_1__trial0/opening_prompt.txt"
    copied = result / "traces" / "stockindex_1__trial0" / "opening_prompt.txt"
    assert copied.is_file() and "sys" in copied.read_text()


def test_bundle_without_usage_or_prompt_stays_compatible(tmp_path: Path) -> None:
    run_dir = tmp_path / "no-usage-run"
    _write_run(run_dir, [("stockindex:1", 0)])
    result = build_bundle(run_dir, output_dir=tmp_path / "bundle-no-usage")
    entry = json.loads((result / "manifest.json").read_text())["trials"][0]
    assert entry["usage"] is None
    assert entry["opening_prompt"] is None

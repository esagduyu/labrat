"""dab_setup Postgres DB names must preserve the config's exact case.

Regression: an unquoted `CREATE DATABASE patent_CPCDefinition` folds to lowercase
in Postgres, but the DAB eval connects with the verbatim config `db_name`, so the
mixed-case dataset (patents) failed with an infra connection error. Setup must
create/check the exact-case name and quote the identifier. (2026-07-20)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.dab_setup as dab_setup


def test_pg_load_dataset_quotes_and_preserves_case(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        # pg_database_exists probe → report "does not exist" so a create is attempted
        stdout = "" if any("pg_database" in a for a in args) else "ok"
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dab_setup.pg_load_dataset("patent_CPCDefinition", Path("/tmp/x.sql"))

    create = next(a for a in calls if any("CREATE DATABASE" in part for part in a))
    stmt = next(part for part in create if "CREATE DATABASE" in part)
    assert stmt == 'CREATE DATABASE "patent_CPCDefinition";'  # quoted + exact case
    # the SQL load connects to the exact-case db name, never a lowercased one
    load = next(a for a in calls if "-f" in a)
    assert "patent_CPCDefinition" in load and "patent_cpcdefinition" not in load


def test_pg_database_exists_checks_exact_case(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(" ".join(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dab_setup.pg_database_exists("patent_CPCDefinition")
    assert any("datname='patent_CPCDefinition'" in s for s in seen)
    assert not any("patent_cpcdefinition" in s for s in seen)

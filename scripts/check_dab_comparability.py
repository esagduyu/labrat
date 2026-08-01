#!/usr/bin/env python3
"""Certify (or refuse to certify) that two DAB runs' numbers are comparable.

Comparing DAB run numbers across time (an ablation arm vs. a stored baseline,
a rerun vs. an earlier one) is only valid if the *code* that produced them
matches — matching flags in config.json is not enough, since the intervening
commits between two runs can change agent behaviour unconditionally for every
arm. This script reads each run's ``provenance`` (see
``src/labrat/eval/benchmarks/dab/provenance.py``) and decides:

* identical code state -> exit 0, "comparable"
* code differs in a way that can affect behaviour -> exit 1, names the paths
* provenance missing/unavailable on either side, OR either side spans more
  than one code state across a resume ("provenance_mixed") -> exit 2,
  REFUSES to certify (never assumes equality from silence — this is the
  important case: most runs on disk predate provenance capture and have none)

Usage:
    uv run python scripts/check_dab_comparability.py RUN_A RUN_B
    uv run python scripts/check_dab_comparability.py RUN_A --live
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from labrat.eval.benchmarks.dab.provenance import (
    ProvenanceDict,
    capture_git_provenance,
    check_comparability,
)

_EXIT_COMPARABLE = 0
_EXIT_CODE_DIFF = 1
_EXIT_PROVENANCE_MISSING = 2


def _load_provenance(run_dir: Path) -> ProvenanceDict | None:
    config_path = run_dir / "config.json"
    if not config_path.exists():
        return None
    try:
        config: dict[str, Any] = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    provenance = config.get("provenance")
    if not isinstance(provenance, dict):
        return None
    return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_a", type=Path, help="First DAB run output directory")
    parser.add_argument(
        "run_b",
        type=Path,
        nargs="?",
        default=None,
        help="Second DAB run output directory (omit with --live)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Compare run_a against the current checkout's live git state instead of a "
        "second run directory (use this to gate a run BEFORE it starts).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repo root to resolve commits/diffs against (default: cwd's repo).",
    )
    args = parser.parse_args(argv)

    if args.live and args.run_b is not None:
        parser.error("--live compares run_a against the live checkout; do not also pass run_b")
    if not args.live and args.run_b is None:
        parser.error("run_b is required unless --live is given")

    provenance_a = _load_provenance(args.run_a)
    label_b: str
    if args.live:
        provenance_b: ProvenanceDict | None = capture_git_provenance(args.repo_root)
        label_b = "current checkout"
    else:
        assert args.run_b is not None
        provenance_b = _load_provenance(args.run_b)
        label_b = str(args.run_b)

    result = check_comparability(
        provenance_a,
        provenance_b,
        repo_root=args.repo_root,
        label_a=str(args.run_a),
        label_b=label_b,
    )

    print(result.reason)
    if result.differing_files:
        for path in result.differing_files:
            print(f"  - {path}")

    if result.verdict == "comparable":
        return _EXIT_COMPARABLE
    if result.verdict in ("provenance_missing", "provenance_mixed"):
        return _EXIT_PROVENANCE_MISSING
    return _EXIT_CODE_DIFF


if __name__ == "__main__":
    sys.exit(main())

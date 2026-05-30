"""Wraps each DAB query's validate.py for safe invocation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def score_with_validator(validator_path: Path, llm_output: str) -> tuple[bool, str | None]:
    """Import the validator module and call validate(llm_output).

    Returns (passed, reason). On import or runtime error returns
    (False, "validator_error: <message>").
    """
    # DAB validators import common_scaffold from the repo root (three dirs up from
    # query_<DATASET>/query<N>/validate.py → repo root).
    dab_root = validator_path.parent.parent.parent
    _inserted = str(dab_root) not in sys.path
    if _inserted:
        sys.path.insert(0, str(dab_root))

    try:
        spec = importlib.util.spec_from_file_location(
            f"dab_validator_{hash(str(validator_path))}", validator_path
        )
        if spec is None or spec.loader is None:
            return (False, f"validator_error: could not load spec from {validator_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as e:
        return (False, f"validator_error: import: {type(e).__name__}: {e}")
    finally:
        if _inserted and str(dab_root) in sys.path:
            sys.path.remove(str(dab_root))

    validate = getattr(module, "validate", None)
    if validate is None:
        return (False, "validator_error: no `validate` function in module")

    try:
        result = validate(llm_output)
    except Exception as e:
        return (False, f"validator_error: runtime: {type(e).__name__}: {e}")

    if isinstance(result, bool):
        return (result, None)
    try:
        passed_raw, reason_raw = result  # type: ignore[misc]
        return (bool(passed_raw), str(reason_raw) if reason_raw else None)
    except (TypeError, ValueError):
        return (False, f"validator_error: unexpected return shape: {type(result).__name__}")

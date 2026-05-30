from pathlib import Path

from labrat.eval.benchmarks.dab.scorer import score_with_validator


def _write_validator(path: Path, body: str) -> None:
    path.write_text(body)


def test_validator_returns_pass(tmp_path: Path) -> None:
    v = tmp_path / "validate.py"
    _write_validator(v, "def validate(out: str):\n    return ('foo' in out, 'expected foo')\n")
    passed, _reason = score_with_validator(v, "the foo bar")
    assert passed is True


def test_validator_returns_fail(tmp_path: Path) -> None:
    v = tmp_path / "validate.py"
    _write_validator(v, "def validate(out: str):\n    return ('foo' in out, 'expected foo')\n")
    passed, reason = score_with_validator(v, "no f-word here")
    assert passed is False
    assert reason


def test_validator_with_runtime_error_returns_validator_error(tmp_path: Path) -> None:
    v = tmp_path / "validate.py"
    _write_validator(v, "def validate(out: str):\n    raise RuntimeError('boom')\n")
    passed, reason = score_with_validator(v, "anything")
    assert passed is False
    assert reason is not None
    assert reason.startswith("validator_error")


def test_validator_with_import_error_returns_validator_error(tmp_path: Path) -> None:
    v = tmp_path / "validate.py"
    _write_validator(
        v,
        "import this_module_does_not_exist\ndef validate(out: str): return (True, '')\n",
    )
    passed, reason = score_with_validator(v, "anything")
    assert passed is False
    assert reason is not None
    assert reason.startswith("validator_error")


def test_validator_missing_function_returns_validator_error(tmp_path: Path) -> None:
    v = tmp_path / "validate.py"
    _write_validator(v, "x = 1\n")
    passed, reason = score_with_validator(v, "anything")
    assert passed is False
    assert reason is not None
    assert reason.startswith("validator_error")

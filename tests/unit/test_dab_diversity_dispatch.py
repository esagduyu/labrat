from labrat.eval.benchmarks.dab.suite import _CONSENSUS_FRAMINGS, _framing_for


def test_framing_none_is_empty() -> None:
    assert _framing_for(None) == ""


def test_framing_rotates() -> None:
    assert _framing_for(0) == _CONSENSUS_FRAMINGS[0]
    assert _framing_for(len(_CONSENSUS_FRAMINGS)) == _CONSENSUS_FRAMINGS[0]  # wraps
    assert _framing_for(1) != _framing_for(0)


def test_framings_are_process_only() -> None:
    joined = " ".join(_CONSENSUS_FRAMINGS).lower()
    for banned in ("ground truth", "the answer is"):
        assert banned not in joined

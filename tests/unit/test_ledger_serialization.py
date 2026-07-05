"""ModelVisibleToolResult rendering + the explicit tool-payload contract."""

from __future__ import annotations

from labrat.agent.tools.serialization import (
    LedgerPayloadKind,
    LedgerPayloadProvider,
    ModelVisibleToolResult,
    render,
)


def test_render_passthrough_is_exactly_the_preview() -> None:
    mvtr = ModelVisibleToolResult(summary="", preview="ok=True rows=[['1']]", truncated=False)
    assert render(mvtr) == "ok=True rows=[['1']]"


def test_render_truncated_frames_summary_ref_and_preview() -> None:
    mvtr = ModelVisibleToolResult(
        summary="run_sql: 1000 rows × 2 columns (a, b); first 50 shown; full result stored.",  # noqa: RUF001
        preview="a\tb\n1\tx",
        artifact_ref="result://sess/0000",
        full_row_count=1000,
        truncated=True,
    )
    rendered = render(mvtr)
    lines = rendered.splitlines()
    assert lines[0].startswith("[context ledger] run_sql: 1000 rows")
    assert "full_row_count: 1000" in lines
    assert "artifact_ref: result://sess/0000" in lines
    assert rendered.endswith("preview:\na\tb\n1\tx")


def test_render_truncated_without_row_count_omits_line() -> None:
    mvtr = ModelVisibleToolResult(
        summary="big: 20000-byte text output.",
        preview="xxx",
        artifact_ref="result://sess/0001",
        truncated=True,
    )
    rendered = render(mvtr)
    assert "full_row_count" not in rendered
    assert "artifact_ref: result://sess/0001" in rendered


def test_ledger_payload_provider_is_duck_typed() -> None:
    class _Hooked:
        def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
            return ("json", {"k": 1})

    class _Plain:
        pass

    assert isinstance(_Hooked(), LedgerPayloadProvider)
    assert not isinstance(_Plain(), LedgerPayloadProvider)
    assert not isinstance("a string", LedgerPayloadProvider)

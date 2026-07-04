"""C4.2: bounded self-critique prune pass (fail-open)."""

from __future__ import annotations

from labrat.maze.cartographer import prune_unsupported
from labrat.maze.document import ScentDoc, Section


async def test_prune_drops_unsupported_bullet() -> None:
    skeleton = ScentDoc(
        domain="x",
        sections=[Section(heading="Key Tables", body="- t has column foo", source="verified")],
    )
    prose = [
        Section(
            heading="Gotchas",
            body="- WHEN X, use foo.\n- WHEN Y, use invented_col.",
            source="draft",
        )
    ]

    async def _llm(prompt: str) -> str:
        return "- WHEN X, use foo."

    kept = await prune_unsupported(skeleton, prose, _llm)
    body = "\n".join(s.body for s in kept)
    assert "WHEN X, use foo." in body
    assert "invented_col" not in body


async def test_prune_fail_open_on_error() -> None:
    skeleton = ScentDoc(domain="x", sections=[])
    prose = [Section(heading="Gotchas", body="- a\n- b", source="draft")]

    async def _boom(prompt: str) -> str:
        raise RuntimeError("llm down")

    kept = await prune_unsupported(skeleton, prose, _boom)
    assert kept == prose  # full draft kept on error


async def test_prune_fail_open_on_empty_response() -> None:
    skeleton = ScentDoc(domain="x", sections=[])
    prose = [Section(heading="Gotchas", body="- a\n- b", source="draft")]

    async def _empty(prompt: str) -> str:
        return "   "

    kept = await prune_unsupported(skeleton, prose, _empty)
    assert kept == prose  # unparseable / kept-nothing -> fail-open

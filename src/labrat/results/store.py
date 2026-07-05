"""ResultStore: addressable on-disk store for over-budget tool payloads.

Artifacts are the provenance backbone ("Cheese"): tables → Parquet + a JSON
metadata sidecar, profile snapshots → JSON, traces → JSONL. Every put returns
an opaque ``artifact_ref`` ("result://<session>/<n>") that ``get`` resolves
back. Purely mechanical — no LLM anywhere in this module.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl


def cap_bytes(text: str, max_bytes: int) -> str:
    """Truncate to at most ``max_bytes`` of UTF-8. Strict — no suffix marker.

    Truncation is signalled by the caller (ModelVisibleToolResult.truncated /
    the mechanical summary), not by mutating the preview past its budget.
    """
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def render_table_head(df: pl.DataFrame, max_rows: int) -> str:
    """Deterministic TSV rendering: header line + the first ``max_rows`` rows."""
    lines = ["\t".join(df.columns)]
    for row in df.head(max_rows).iter_rows():
        lines.append("\t".join("" if v is None else str(v) for v in row))
    return "\n".join(lines)


class ResultStore:
    """Per-session artifact directory under a caller-provided root."""

    def __init__(self, root: Path, *, session: str | None = None) -> None:
        if session is not None and ("/" in session or "\\" in session or ".." in session):
            raise ValueError(
                f"invalid session (must not contain path separators or '..'): {session!r}"
            )
        self._session = session if session is not None else uuid.uuid4().hex[:8]
        self._dir = Path(root) / self._session
        self._dir.mkdir(parents=True, exist_ok=True)
        self._next_id = 0
        self._entries: dict[int, tuple[str, Path]] = {}  # n -> (kind, path)

    @property
    def session(self) -> str:
        return self._session

    @property
    def directory(self) -> Path:
        return self._dir

    # ── writers ───────────────────────────────────────────────────────────────

    def put_table(self, df: pl.DataFrame, *, meta: dict[str, Any] | None = None) -> str:
        """Store a DataFrame as Parquet + a JSON metadata sidecar; return its ref."""
        n = self._claim()
        path = self._dir / f"{n:04d}.table.parquet"
        df.write_parquet(path)
        sidecar: dict[str, Any] = {
            **(meta or {}),
            "columns": df.columns,
            "dtypes": [str(t) for t in df.dtypes],
            "row_count": df.height,
        }
        (self._dir / f"{n:04d}.table.meta.json").write_text(
            json.dumps(sidecar, default=str), encoding="utf-8"
        )
        self._entries[n] = ("table", path)
        return self._ref(n)

    def put_json(self, obj: object, kind: Literal["json", "trace"] = "json") -> str:
        """Store a JSON payload (kind="json") or a JSONL trace (kind="trace")."""
        n = self._claim()
        if kind == "trace":
            if not isinstance(obj, list):
                raise TypeError("trace payload must be a list of JSON-serialisable items")
            items = cast("list[object]", obj)
            path = self._dir / f"{n:04d}.trace.jsonl"
            lines = [json.dumps(item, default=str) for item in items]
            path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            self._entries[n] = ("trace", path)
            return self._ref(n)
        path = self._dir / f"{n:04d}.json"
        path.write_text(json.dumps(obj, default=str), encoding="utf-8")
        self._entries[n] = ("json", path)
        return self._ref(n)

    # ── readers ───────────────────────────────────────────────────────────────

    def get(self, ref: str) -> object:
        """Resolve a ref back to its stored payload.

        table → pl.DataFrame; json → the parsed object; trace → list of parsed items.
        """
        kind, path = self._resolve(ref)
        if kind == "table":
            return pl.read_parquet(path)
        if kind == "trace":
            return [
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line
            ]
        return json.loads(path.read_text(encoding="utf-8"))

    def meta(self, ref: str) -> dict[str, Any] | None:
        """Return the JSON metadata sidecar for a table ref; None for other kinds."""
        kind, path = self._resolve(ref)
        if kind != "table":
            return None
        sidecar = path.with_name(path.stem + ".meta.json")
        data: object = json.loads(sidecar.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return cast(dict[str, Any], data)
        return None

    def preview(self, ref: str, *, max_rows: int = 50, max_bytes: int = 8000) -> str:
        """Bounded human/model-readable preview of an artifact (row AND byte capped)."""
        kind, path = self._resolve(ref)
        if kind == "table":
            return cap_bytes(render_table_head(pl.read_parquet(path), max_rows), max_bytes)
        if kind == "trace":
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
            return cap_bytes("\n".join(lines[:max_rows]), max_bytes)
        return cap_bytes(path.read_text(encoding="utf-8"), max_bytes)

    # ── internals ─────────────────────────────────────────────────────────────

    def _claim(self) -> int:
        n = self._next_id
        self._next_id += 1
        return n

    def _ref(self, n: int) -> str:
        return f"result://{self._session}/{n:04d}"

    def _resolve(self, ref: str) -> tuple[str, Path]:
        prefix = f"result://{self._session}/"
        if not ref.startswith(prefix):
            raise ValueError(f"unknown artifact_ref: {ref!r}")
        try:
            n = int(ref.removeprefix(prefix))
        except ValueError as exc:
            raise ValueError(f"unknown artifact_ref: {ref!r}") from exc
        if n not in self._entries:
            raise ValueError(f"unknown artifact_ref: {ref!r}")
        return self._entries[n]

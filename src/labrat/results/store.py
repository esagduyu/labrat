"""ResultStore: addressable on-disk store for over-budget tool payloads.

Artifacts are the provenance backbone ("Cheese"): tables → Parquet + a JSON
metadata sidecar. Every put returns an opaque ``artifact_ref``
("result://<session>/<n>") that ``get`` resolves back. Purely mechanical —
no LLM anywhere in this module.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import polars as pl


class ResultStore:
    """Per-session artifact directory under a caller-provided root."""

    def __init__(self, root: Path, *, session: str | None = None) -> None:
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
            "columns": df.columns,
            "dtypes": [str(t) for t in df.dtypes],
            "row_count": df.height,
            **(meta or {}),
        }
        (self._dir / f"{n:04d}.table.meta.json").write_text(
            json.dumps(sidecar, default=str), encoding="utf-8"
        )
        self._entries[n] = ("table", path)
        return self._ref(n)

    # ── readers ───────────────────────────────────────────────────────────────

    def get(self, ref: str) -> object:
        """Resolve a ref back to its stored payload (table refs → pl.DataFrame)."""
        kind, path = self._resolve(ref)
        if kind == "table":
            return pl.read_parquet(path)
        raise ValueError(f"unknown artifact_ref: {ref!r}")

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

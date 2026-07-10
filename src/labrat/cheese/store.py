"""Cheese stores: pin-time finding data capture + versioned artifact store.

FindingDataStore snapshots a bounded copy of a finding's results (and its
chart PNG) at PIN time, so export never needs a live DB or agent session.
CheeseStore holds immutable rendered artifacts with a linear version history
and a rollback pointer. No LLM, no network, local files only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl

from labrat.cheese.model import CheeseManifest, CheeseVersion

DEFAULT_DATA_ROOT = Path.home() / ".local" / "share" / "labrat" / "cheese_data"
DEFAULT_CHEESE_ROOT = Path.home() / ".local" / "share" / "labrat" / "cheese"

_ROW_CAP = 50
_REF_PREFIX = "cheese://"


def _is_safe_finding_id(fid: str) -> bool:
    return bool(fid) and "/" not in fid and "\\" not in fid and ".." not in fid


class FindingDataStore:
    """Bounded per-finding results + chart snapshots, keyed by finding id."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def capture(self, finding_id: str, df: pl.DataFrame, *, chart_png: bytes | None = None) -> str:
        if not _is_safe_finding_id(finding_id):
            raise ValueError(f"invalid finding_id: {finding_id!r}")
        self._root.mkdir(parents=True, exist_ok=True)
        df.head(_ROW_CAP).write_parquet(self._root / f"{finding_id}.parquet")
        meta = {"total_rows": df.height, "columns": df.columns}
        (self._root / f"{finding_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        if chart_png is not None:
            (self._root / f"{finding_id}.chart.png").write_bytes(chart_png)
        return f"{_REF_PREFIX}{finding_id}"

    def _finding_id(self, ref: str) -> str | None:
        if not ref.startswith(_REF_PREFIX):
            return None
        fid = ref.removeprefix(_REF_PREFIX)
        if not _is_safe_finding_id(fid):
            return None
        return fid

    def load(self, ref: str) -> tuple[pl.DataFrame, int] | None:
        fid = self._finding_id(ref)
        if fid is None:
            return None
        parquet = self._root / f"{fid}.parquet"
        meta_path = self._root / f"{fid}.meta.json"
        if not parquet.exists() or not meta_path.exists():
            return None
        try:
            raw: object = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            return None
        total: object = (
            cast(dict[str, Any], raw).get("total_rows") if isinstance(raw, dict) else None
        )
        if not isinstance(total, int):
            return None
        return pl.read_parquet(parquet), total

    def load_chart_png(self, ref: str) -> bytes | None:
        fid = self._finding_id(ref)
        if fid is None:
            return None
        png = self._root / f"{fid}.chart.png"
        return png.read_bytes() if png.exists() else None


class CheeseStore:
    """Versioned artifact store: <root>/<cheese_id>/{manifest.json, v<N>.html}."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _dir(self, cheese_id: str) -> Path:
        return self._root / cheese_id

    def _manifest_path(self, cheese_id: str) -> Path:
        return self._dir(cheese_id) / "manifest.json"

    def _save(self, manifest: CheeseManifest) -> None:
        self._manifest_path(manifest.cheese_id).write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )

    def get(self, cheese_id: str) -> CheeseManifest | None:
        path = self._manifest_path(cheese_id)
        if not path.exists():
            return None
        return CheeseManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def create_or_get(
        self, kind: Literal["single", "report"], finding_ids: list[str], title: str
    ) -> CheeseManifest:
        digest = hashlib.sha256(("\x00".join([kind, *finding_ids])).encode()).hexdigest()[:12]
        existing = self.get(digest)
        if existing is not None:
            return existing
        manifest = CheeseManifest(
            cheese_id=digest,
            kind=kind,
            finding_ids=list(finding_ids),
            title=title,
            versions=[],
            current=0,
        )
        self._dir(digest).mkdir(parents=True, exist_ok=True)
        self._save(manifest)
        return manifest

    def add_version(self, cheese_id: str, html: str, rows_mode: Literal["preview", "none"]) -> Path:
        manifest = self.get(cheese_id)
        if manifest is None:
            raise ValueError(f"unknown cheese_id: {cheese_id!r}")
        n = len(manifest.versions) + 1
        path = self._dir(cheese_id) / f"v{n}.html"
        if path.exists():  # immutability guard — never rewrite a version file
            raise FileExistsError(str(path))
        path.write_text(html, encoding="utf-8")
        manifest.versions.append(
            CheeseVersion(
                n=n, exported_at=datetime.now(tz=UTC), path=path.name, rows_mode=rows_mode
            )
        )
        manifest.current = n
        self._save(manifest)
        return path

    def rollback(self, cheese_id: str, n: int) -> None:
        manifest = self.get(cheese_id)
        if manifest is None:
            raise ValueError(f"unknown cheese_id: {cheese_id!r}")
        if not 1 <= n <= len(manifest.versions):
            raise ValueError(f"version {n} out of range 1..{len(manifest.versions)}")
        manifest.current = n
        self._save(manifest)

    def version_path(self, cheese_id: str, n: int) -> Path:
        return self._dir(cheese_id) / f"v{n}.html"

    def list_cheeses(self) -> list[CheeseManifest]:
        if not self._root.exists():
            return []
        out: list[tuple[float, CheeseManifest]] = []
        for sub in self._root.iterdir():
            mp = sub / "manifest.json"
            if not mp.is_file():
                continue
            try:
                manifest = CheeseManifest.model_validate_json(mp.read_text())
            except ValueError:
                continue  # corrupt manifest — skip rather than brick the whole listing
            out.append((mp.stat().st_mtime, manifest))
        out.sort(key=lambda t: t[0], reverse=True)
        return [m for _, m in out]

"""Tests for dbt manifest watcher (M30)."""

from __future__ import annotations

import time
from pathlib import Path


def test_watcher_start_stop(tmp_path: Path) -> None:
    from labrat.catalog.dbt.watcher import ManifestWatcher

    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    called: list[bool] = []
    watcher = ManifestWatcher(manifest_path=manifest, on_change=lambda: called.append(True))
    watcher.start()
    watcher.stop()
    # No exception = success


def test_watcher_calls_callback_on_change(tmp_path: Path) -> None:
    from labrat.catalog.dbt.watcher import ManifestWatcher

    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"metadata": {}}')
    called: list[bool] = []
    watcher = ManifestWatcher(manifest_path=manifest, on_change=lambda: called.append(True))
    watcher.start()
    try:
        time.sleep(0.1)
        manifest.write_text('{"metadata": {"updated": true}}')
        time.sleep(0.5)
    finally:
        watcher.stop()
    assert len(called) >= 1

"""Tests for the optional embedding layer (hybrid RRF retrieval, T2b v2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from labrat.maze.embedding import (
    Embedder,
    SectionEmbeddingCache,
    body_key,
    get_default_embedder,
)


class _StubEmbedder:
    """Deterministic fake: vector = [len(text), count of 'revenue', count of 'chart']."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    @property
    def model_id(self) -> str:
        return "stub-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(t)), float(t.count("revenue")), float(t.count("chart"))] for t in texts]


def test_stub_satisfies_protocol() -> None:
    embedder: Embedder = _StubEmbedder()
    assert embedder.embed(["a"]) == [[1.0, 0.0, 0.0]]


def test_body_key_is_stable_sha256() -> None:
    assert body_key("abc") == body_key("abc")
    assert body_key("abc") != body_key("abd")
    assert len(body_key("abc")) == 64


def test_cache_embeds_once_then_serves_from_sidecar(tmp_path: Path) -> None:
    sidecar = tmp_path / ".embeddings.jsonl"
    first = SectionEmbeddingCache(sidecar, _StubEmbedder())
    v1 = first.get_or_embed(["revenue by month", "user churn"])
    assert v1[0][1] == 1.0  # 'revenue' counted

    replay_embedder = _StubEmbedder()
    second = SectionEmbeddingCache(sidecar, replay_embedder)
    v2 = second.get_or_embed(["revenue by month", "user churn"])
    assert v2 == v1
    assert replay_embedder.calls == []  # served entirely from the sidecar


def test_cache_reembeds_stale_body_and_ignores_corrupt_lines(tmp_path: Path) -> None:
    sidecar = tmp_path / ".embeddings.jsonl"
    cache = SectionEmbeddingCache(sidecar, _StubEmbedder())
    cache.get_or_embed(["original body"])
    # Corrupt line + a stale/foreign entry must both be tolerated.
    with sidecar.open("a", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write(json.dumps({"model": "other-model", "key": body_key("x"), "vector": [1.0]}) + "\n")

    fresh = _StubEmbedder()
    cache2 = SectionEmbeddingCache(sidecar, fresh)
    out = cache2.get_or_embed(["original body", "a brand new body"])
    assert out[0] == [float(len("original body")), 0.0, 0.0]
    assert fresh.calls == [["a brand new body"]]  # only the new body embeds


def test_cache_write_failure_is_nonfatal(tmp_path: Path) -> None:
    unwritable = tmp_path / "missing-parent" / ".embeddings.jsonl"  # parent does not exist
    cache = SectionEmbeddingCache(unwritable, _StubEmbedder())
    out = cache.get_or_embed(["revenue"])
    assert out == [[7.0, 1.0, 0.0]]


def test_get_default_embedder_fails_open_without_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    # get_default_embedder() loads model2vec via importlib.import_module; simulate
    # the optional `semantic` extra being absent regardless of whether it is
    # actually installed in this environment (it is once local-embed classify is
    # in use). Patching importlib.import_module — not builtins.__import__ — is
    # required: a cached model2vec in sys.modules bypasses builtins.__import__.
    import importlib

    real_import_module = importlib.import_module

    def _no_model2vec(name: str, *args: object, **kwargs: object) -> object:
        if name == "model2vec":
            raise ImportError("model2vec is not installed")
        return real_import_module(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(importlib, "import_module", _no_model2vec)
    assert get_default_embedder() is None

"""Optional embedding layer for hybrid retrieval (T2b v2).

Local-first by construction: the only supported backend is a local
static-embedding model (model2vec, behind the ``labrat[semantic]`` extra).
Every failure path degrades to ``None`` so callers fall back to pure-lexical
retrieval — the base install never gains a hard dependency and the benchmark
path (flag off) never reaches this module.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
from typing import Any, Protocol, cast

#: Default model2vec model; override with LABRAT_EMBED_MODEL (local dir or HF id).
DEFAULT_MODEL_ID = "minishlab/potion-base-8M"


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def body_key(text: str) -> str:
    """Stable content key for a section body (sha256 hex)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _StaticEmbedder:
    """model2vec-backed embedder: pure-numpy inference, offline after first fetch."""

    def __init__(self, model_id: str, model: Any) -> None:
        self._model_id = model_id
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model_id

    def embed(self, texts: list[str]) -> list[list[float]]:
        raw = self._model.encode(texts)
        return [[float(v) for v in row] for row in raw]


def get_default_embedder() -> Embedder | None:
    """Load the optional local embedder; any failure fails open to None.

    The one-time model fetch (when LABRAT_EMBED_MODEL names a hub id that is not
    cached yet) is provisioning, not retrieval: retrieval itself is offline.
    """
    model_id = os.environ.get("LABRAT_EMBED_MODEL") or DEFAULT_MODEL_ID
    try:
        module: Any = importlib.import_module("model2vec")  # optional [semantic] extra
        model: Any = module.StaticModel.from_pretrained(model_id)
    except Exception:
        return None
    return _StaticEmbedder(model_id, model)


class SectionEmbeddingCache:
    """A ``.embeddings.jsonl`` sidecar keyed by (model_id, sha256(body)).

    Reads tolerate corrupt or foreign-model lines; writes are best-effort
    (an unwritable sidecar degrades to in-memory-only for the process).
    """

    def __init__(self, path: Path, embedder: Embedder) -> None:
        self._path = path
        self._embedder = embedder
        self._mem: dict[str, list[float]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            data = cast("dict[str, Any]", entry)
            model = data.get("model")
            key = data.get("key")
            vector = data.get("vector")
            if model != self._embedder.model_id or not isinstance(key, str):
                continue
            if not isinstance(vector, list):
                continue
            items = cast("list[Any]", vector)
            if not all(isinstance(v, (int, float)) for v in items):
                continue
            self._mem[key] = [float(cast("float", v)) for v in items]

    def get_or_embed(self, texts: list[str]) -> list[list[float]]:
        self._load()
        keys = [body_key(t) for t in texts]
        missing = [(k, t) for k, t in zip(keys, texts, strict=True) if k not in self._mem]
        if missing:
            vectors = self._embedder.embed([t for _, t in missing])
            new_entries: list[str] = []
            for (k, _), vec in zip(missing, vectors, strict=True):
                self._mem[k] = vec
                new_entries.append(
                    json.dumps({"model": self._embedder.model_id, "key": k, "vector": vec})
                )
            try:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write("\n".join(new_entries) + "\n")
            except OSError:
                pass  # best-effort sidecar; in-memory copy still serves this process
        return [self._mem[k] for k in keys]

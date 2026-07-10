"""Cheese v1 models: provenance snapshots + versioned-artifact manifests."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ScentSourceRef(BaseModel):
    """One Scent doc consulted during the turn, as shown to the recipient."""

    domain: str
    tier: str | None
    fresh: bool | None


class FindingProvenance(BaseModel):
    """Trust-block snapshot captured at pin time. Never inferred at render time."""

    scent_sources: list[ScentSourceRef]
    joins_verified: int
    lineage_used: bool
    verifier_verdict: str | None
    run_sql_count: int
    schema_fingerprint: str | None
    git_sha: str | None
    model_id: str | None
    captured_at: datetime


class CheeseVersion(BaseModel):
    n: int
    exported_at: datetime
    path: str  # relative to the cheese directory
    rows_mode: Literal["preview", "none"]


class CheeseManifest(BaseModel):
    cheese_id: str
    kind: Literal["single", "report"]
    finding_ids: list[str]
    title: str
    versions: list[CheeseVersion]
    current: int

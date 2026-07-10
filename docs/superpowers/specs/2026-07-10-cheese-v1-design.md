# Cheese v1 — Pillar 2 Share Surface — Design

**Date:** 2026-07-10 · **Status:** approved (interactive brainstorm; all product decisions user-ratified)
**Thesis:** a Finding exports as a **free, provenance-stamped, self-contained HTML artifact** viewable by someone who has never installed LabRat. The artifact is the top of the adoption funnel (commercial-model memo #24, Option B — Cheese v1 free + standalone-viewable is a hard constraint). This is the first Pillar 2 build; nothing in it may depend on the (future, paid) team layer.

## 1. What already holds (verified in source this session)

- `thread/model.py::Finding` — `id, version_id, question, sql, results_ref, chart_spec, note, pinned_at`. No provenance field.
- `thread/findings.py::FindingsManager` — ordered JSON list at `~/.local/share/labrat/findings.json`; `pin/unpin/update_note/reorder`.
- `audit/export.py::export_findings` (M20) — self-contained HTML but renders **question + note + SQL only**; no results, no chart, no provenance. Sole caller: `screens/findings_viewer.py`.
- `results/store.py::ResultStore` — resolves `results_ref` → Polars DataFrame (Parquet-backed); has `preview`/`render_table_head` helpers.
- `widgets/turn_provenance.py::TurnProvenance` — already accumulates per-turn scent hits (domain/tier/fresh), `verify_join`/`explain_lineage` usage, `run_sql` count, verifier outcome; renders the one-line ⚑ footer. Never-raise contract.
- matplotlib is already a dependency (chart stack).

## 2. User-ratified product decisions

- **D1 — Unit: BOTH.** Single-Finding artifact (the viral atom) AND the curated multi-finding report, sharing one rendering layer.
- **D2 — Data: bounded preview by default.** Chart + up to **50 rows** + total row count. Per-export `rows_mode="none"` strips rows (SQL + chart + summary only). Full-results embedding does **not exist** in v1.
- **D3 — Provenance: full trust block.** Per finding: scent sources (domain · tier · fresh/stale), joins-verified count, lineage-used flag, verifier verdict (if run), `run_sql` count, schema fingerprint, `git_sha`, model id, captured-at. Findings pinned before capture render **"unattested (pinned before provenance capture)"** — honest, never fabricated.
- **D4 — Share UX: both entry points.** Findings viewer (per-finding export + upgraded report export + version browser) AND a chat-level "share this answer" action = pin last turn (with provenance snapshot) + export single + notify path. Sugar over pin+export, not a separate path.
- **D5 — Architecture: new `src/labrat/cheese/` package** (Approach 1). `audit/export.py` retires; `findings_viewer` migrates to the new renderer.
- **D6 — Versioning (user addition):** author-side linear version history per Cheese with rollback, Claude-artifacts-style. Re-export of the same Cheese bumps v<N+1>; version files are immutable; `current` pointer can be rolled back to any older version; iterating after rollback continues at v<N+1> (linear, no branches). Recipient-side in-file version dropdown is OUT (file bloat).

## 3. Components

### 3.1 `cheese/model.py`
- `FindingProvenance(BaseModel)`: `scent_sources: list[ScentSourceRef]` (`domain: str`, `tier: str | None`, `fresh: bool | None`), `joins_verified: int`, `lineage_used: bool`, `verifier_verdict: str | None`, `run_sql_count: int`, `schema_fingerprint: str | None`, `git_sha: str | None`, `model_id: str | None`, `captured_at: datetime`.
- `CheeseVersion(BaseModel)`: `n: int`, `exported_at: datetime`, `path: str` (relative to the cheese dir), `rows_mode: Literal["preview", "none"]`.
- `CheeseManifest(BaseModel)`: `cheese_id: str`, `kind: Literal["single", "report"]`, `finding_ids: list[str]`, `title: str`, `versions: list[CheeseVersion]`, `current: int`.
- `thread/model.py::Finding` gains `provenance: FindingProvenance | None = None` (back-compat: old JSON deserializes to `None`).

### 3.2 `cheese/store.py` — versioned export store
- Root `~/.local/share/labrat/cheese/<cheese_id>/` containing `manifest.json` + `v<N>.html`.
- `CheeseStore(root)`: `create_or_get(kind, finding_ids, title) -> CheeseManifest` (identity = kind + ordered finding_ids; same set → same cheese_id, new set → new cheese), `add_version(cheese_id, html, rows_mode) -> Path` (writes v<N+1>, updates manifest, sets `current=N+1`), `rollback(cheese_id, n)` (sets `current`; no file changes), `list_cheeses() -> list[CheeseManifest]`, `version_path(cheese_id, n) -> Path`.
- Version files are never overwritten or deleted by the store.

### 3.3 `cheese/render.py` — the one renderer
- `render_cheese(findings: list[FindingRender], *, title, kind, version_n, exported_at) -> str` — one Jinja template renders 1..N findings; single vs report differ only in count and header copy.
- `FindingRender` (plain dataclass built by export): finding fields + `chart_png_b64: str | None` + `table_rows: list[list[str]] | None` + `table_columns: list[str] | None` + `total_rows: int | None`.
- Per finding: question, note, chart `<img>` (data-URI), bounded table (or "rows omitted at export" for `rows_mode="none"`), SQL inside collapsed `<details>`, the full trust block (D3), or the unattested line.
- Page-level: version stamp ("v3 · exported 2026-07-11"), funnel footer "Made with LabRat — <repo link>".
- Fully self-contained: inline CSS, no external assets, no JS required; every user string escaped.

### 3.4 `cheese/export.py` — orchestration
- `export_cheese(findings, *, kind, title, rows_mode="preview", store, result_store) -> Path`:
  resolve each `results_ref` → `df.head(50)` + `len(df)` (missing/unresolvable ref → table omitted with an honest "results unavailable" note, never an exception); `chart_spec` → matplotlib Agg → PNG → base64 (chart failure → chart omitted, noted); render; `store.add_version`; return the written path.
- Export must never raise into the TUI: any per-finding degradation is rendered honestly in the artifact.

### 3.5 Provenance capture
- `TurnProvenance.snapshot() -> FindingProvenance | None` — structured export of what the widget already accumulates (returns `None` when the turn had no activity). Rendering path untouched; never-raise contract preserved.
- `FindingsManager.pin(..., provenance: FindingProvenance | None = None)`; `MainScreen`'s pin action passes the current turn's snapshot.

### 3.6 TUI surface
- **Findings viewer:** per-finding "export Cheese (single)"; report export migrated to the new renderer (multi-select include/exclude deferred — v1 report = all pinned findings, matching today); a version browser per Cheese (list versions → open path / re-share / roll back).
- **Chat:** "share this answer" binding on `MainScreen` → pin last completed turn (question = last user prompt, sql = `_last_sql`, results_ref/chart_spec when present, provenance = snapshot) → `export_cheese(kind="single")` → notify with the file path.
- Keybindings chosen at plan time against the live binding map; prefer plain free keys (ctrl+shift chords are terminal-dependent — known caveat).

## 4. Non-negotiables

1. **Standalone-viewable:** the artifact opens in a browser with zero network access and zero LabRat installed. No external assets, ever.
2. **Nothing leaves the machine:** export writes a local file; no upload, no telemetry, no remote calls.
3. **Provenance is never fabricated:** no snapshot → "unattested", verbatim tier/freshness from capture, no inference at render time.
4. **Bounded by construction:** ≤50 rows per finding regardless of result size; `rows_mode="none"` available at every export site.
5. **Version immutability:** an existing `v<N>.html` is never rewritten; rollback only moves the `current` pointer.
6. **Benchmark isolation:** nothing under `eval/` or `mcp/` imports `cheese/`.
7. **Export never raises into the TUI**; per-finding failures degrade to honest omissions in the artifact.
8. Pyright strict (`cheese/`, `thread/`, `widgets/`); `screens/` exempt as usual; repo gates per commit; `test_app_renders` env flake is non-signal.

## 5. Testing

- Renderer: single + report, attested (full block substrings: tier, fresh/stale, verdict) + unattested line, `rows_mode` both values, chart embed from a tiny DataFrame (`data:image/png;base64,` present), escaping (question containing `<script>` renders escaped).
- Store: linear version bump, rollback + iterate-after-rollback (v pointer semantics), immutability (bytes of v1 unchanged after v2), manifest round-trip, identity rule (same finding set → same cheese, different set → new).
- Export: missing `results_ref` → honest omission; chart failure → honest omission; bounded head(50) + total count.
- Pin path: snapshot captured and persisted through `findings.json` round-trip; old-format findings load with `provenance=None`.
- TUI: pilot tests for both entry points; pty-harness manual gate (share-from-chat + viewer export + version rollback; open artifact and verify content).

## 6. Out of scope (v1, explicit)

Hosted/remote links or any network share; recipient-side in-file version dropdown; team Cheese board (paid tier, per memo #24); Markdown export format; column-level redaction; full-results embedding; post-export artifact editing; multi-select report curation UI.

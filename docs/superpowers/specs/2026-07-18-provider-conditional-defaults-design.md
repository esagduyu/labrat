# Provider-Conditional Defaults — Design (Track 2, item 2)

**Status:** Decided 2026-07-18 (autonomous Track-2 planning; decisions recorded in
[`2026-07-18-moat-decisions.md`](2026-07-18-moat-decisions.md) §Part B)
**Author:** Claude Fable (Track-2 planning fork)
**Related:** the per-backbone ablation record (`docs/dab-solultra-ablation.md`,
`docs/dab-progress-report.md`), `src/labrat/profile/model.py` (`Profile.agent_provider/
agent_model` + moat flags), `src/labrat/agent/session.py::build_agent_session`.

## One-sentence pitch

Ship the per-backbone measurements we already publish as an in-product
recommendation table — "for your model, we recommend these settings, and here's the
measurement" — so "provider-aware grounding" is a feature users touch, not a slogan.

## Why (the receipts already exist)

Twice-confirmed asymmetries: Cartographer +8pp on Sonnet but regresses GPT-5.x alone
while interacting positively inside the full GPT stack (Arm A, 2026-07-16); levers
+8pp Sonnet / neutral GPT; verify measured no benefit on either. Competitors ship
one-size-fits-all; the board's own two-entries-per-team rows (Spacedock GPT 74.33 vs
Opus 67.21) prove the asymmetry is universal — only we measure it.

## Design

### 1. The table (`src/labrat/agent/defaults.py`)

```python
class Receipt(BaseModel):
    claim: str          # "Verifier measured no accuracy benefit (49.3 vs 49.1)"
    doc: str            # repo-relative path, e.g. "docs/dab-progress-report.md"
    measured_on: str    # ISO date of the measurement

class RecommendedDefaults(BaseModel):
    family: str                      # glob: "claude-sonnet-*", "gpt-5.*", ...
    reasoning: str | None = None     # provider effort ("max", "high", None=provider default)
    verify: bool = False
    hybrid_retrieval: bool | None = None   # None = no recommendation
    enable_ledger: bool = True
    classify_tier: str | None = None # model id hint for llm_classify ("claude-haiku-4-5", ...)
    receipts: list[Receipt]

RECOMMENDED_DEFAULTS: tuple[RecommendedDefaults, ...] = (...)

def resolve_recommended(provider: str, model: str | None) -> RecommendedDefaults | None:
    """First family glob matching the resolved model id; None when unknown."""
```

Seed rows: `claude-sonnet-*`, `claude-opus-*`, `gpt-5.*` — content transcribed from
the shipped ablation docs, each field carrying at least one receipt. Unknown models
resolve to `None` (no recommendation shown — never guess).

### 2. Surfacing (PD2: never auto-applied)

- **Settings screen:** beside each recommended-covered control, a `★ recommended`
  chip when the profile's current value differs; an "Apply recommended" action
  writes the explicit profile fields (normal `ProfileManager` update path — the
  frozen `Profile` model is unchanged). An expandable "why" renders the receipts.
- **CLI:** `labrat defaults show [--profile P]` prints the resolved table +
  receipts; `labrat defaults apply --profile P` performs the same explicit write
  (scriptable parity with the TUI).
- **First-connect nudge:** when a profile's provider/model resolves to a family with
  recommendations and no explicit choice has ever been applied, one status-bar
  notify pointing at Settings (same pattern as the Map seed nudge; once per screen
  instance, never auto-runs anything).

### 3. Update path

A new ablation result updates `RECOMMENDED_DEFAULTS` and its receipts in the same PR
that lands the ablation doc — the table is versioned with the code it measured.
`measured_on` staleness (>6 months) renders a "measurement aging" note in the UI.

## Benchmark-safety proof obligation

`defaults.py` is imported only by `screens/settings.py`, the CLI command, and tests.
Proof: a unit test asserts `labrat.agent.defaults` is not imported (directly or
transitively) by `labrat.eval.benchmarks.*` or `run_agent_task` (import-graph
assertion mirroring the evals-package isolation test); eval/DAB behavior is
byte-identical because nothing on those paths resolves recommendations.

## Non-goals

No remote/config-service table; no per-user telemetry-driven tuning; no auto-apply
(PD2); no attempt to recommend for providers we haven't measured (openai-compatible
third parties resolve `None`).

## Test strategy

Glob resolution (exact, wildcard, unknown→None); receipts present on every seeded
field; apply path writes explicit profile fields and is idempotent; chip logic
(differs vs matches); import-graph isolation test; CLI snapshot of `defaults show`.

## Effort

S–M: table + resolver (½d), Settings chips + apply (1d), CLI (½d), tests/docs (½d).

_Regenerated 2026-07-23 from transcript after accidental deletion._

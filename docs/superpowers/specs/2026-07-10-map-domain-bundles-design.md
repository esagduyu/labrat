# Map — Domain Bundles — Design

**Date:** 2026-07-10 · **Status:** approved in brainstorm (shape + name settled with the user) — supersedes the Warren design-exploration RFC (`2026-07-10-warren-design-exploration.md`). Awaiting user review of this written spec before writing-plans.
**Name decision:** the domain-bundle layer is a **Map** (per-domain), replacing the proposed "Warren." Rationale: it unifies Pillar 3 under the already-shipped **Cartographer** — the Cartographer *draws Maps*, **Scent** marks them, **Trails** route across them, **Cheese** is the destination. "Warren" (a rabbit tunnel-tangle) fought the "focused, mapped region" meaning; "Map" makes the mapmaker make maps. The collection of a user's/team's Maps = "your Maps" / the charted Maze (no separate collective noun needed).

## 1. What a Map is (Meta's Cookbook, in our vocabulary)

A **Map** is a curated **chart of one domain-region of the maze**: which **Scent** (landmarks/grounding) and which **Trails** (proven routes) matter for that domain, plus its governing **Decisions** and starter **prompts**. It is the aggregation layer the north-star flagged as *Missing* — `Scent → Trail → Map`. Concretely, a Map for a B2B-SaaS "Revenue" domain points at the `subscriptions`/`invoices` Scent, the `compute-mrr`/`nrr`/`churn-rate` Trails, the "attribute revenue at contract start" Decisions, and 3–5 starter prompts.

**A Map is a bundle of POINTERS, never content** (the W1 decision). It stores only member *IDs* — Scent domain slugs + Trail intent-slugs + Decision refs — resolved live against the store. This is the entire anti-staleness story: **there is no content to go stale, because there is no content, only addresses.** A dangling reference (a Trail was deleted) is a soft-miss ("route no longer exists"), never stale data and never an error.

## 2. Decisions (settled with the user)

- **W1 — Reference, not copy.** A `kind="map"` doc is pure pointers. The same Scent/Trail belongs to multiple Maps (`subscriptions` is in Revenue, Finance, and CS Maps) — copying would triplicate and drift. Export *materializes* pointers into a portable pack only when shipping a Map off-machine (deferred; §6).
- **W2 — Cartographer auto-sketches, human inks in.** The **Cartographer drafts a Map skeleton from the dbt project structure** (marts folders / dbt groups / tags → the Scent domains for those models become the Map's members). The analyst then curates: adds Trails, records Decisions, writes prompts, renames the region. Auto-seed-then-curate — no blank page, no false domains. This makes the Cartographer the drafter of *both* Scent markers and the regional Maps ("survey the terrain → draw Scent → sketch the Maps → hand off for curation").
- **W3 — Activation is the mechanic; share is distribution; onboarding is a surface.** Activating a Map **scopes the agent's grounding** to that domain — `search_reference_docs`/`search_trails` filter to the Map's members. It is the article's "narrow a million-field warehouse to a few dozen curated files," at domain granularity. Activation is **additive/inheritable**: activate Revenue + Product + Growth together for a cross-domain (growth-analyst) question. Share = a data lead authors a Map once and the team activates it (the team-tier moat). Onboarding = the suggested prompts orient a new hire.

## 3. Components

### 3.1 `maze/map.py` (new) — the Map doc model + resolution
- A Map is a `kind="map"` doc (reuse `MazeStore`/`document.py`; the store already reserves the kind — `warren` reservation is superseded by `map`). Frontmatter: `kind: map`, `domain` = map-slug (e.g. `revenue`), optional display title. Sections: **Overview**, **Scent** (list of Scent domain slugs), **Trails** (list of Trail intent-slugs), **Suggested Prompts**. Note: **Decisions ride along inside the referenced Scent** — a decision promotes into its domain's `## Decisions` Scent section (Q4), so referencing the `subscriptions` Scent domain automatically carries its governing decisions; a Map needs no separate Decisions member list.
- `MapDoc` accessors: `scent_members() -> list[str]`, `trail_members() -> list[str]`, `prompts() -> list[str]`.
- `resolve_members(map_docs, store) -> ResolvedMembers` — resolves the union of active Maps' pointers against the live store; a missing referent is dropped with a recorded soft-miss (never raises).

### 3.2 Activation = a retrieval FILTER (the benchmark-safety linchpin)
- An **active-Maps set** (session/profile state; empty by default). When non-empty, `search_reference_docs` filters its Scent doc set to the union of active Maps' `scent_members`, and `search_trails` filters to the union of `trail_members`. **The scorer is unchanged — this is a pre-filter on *which docs are eligible*, not how they rank.**
- When the active set is **empty (the default, and every benchmark run)**, retrieval is over all docs — **byte-identical to today.** The benchmark path never activates a Map (the Cartographer pre-pass writes Scent/sketches Maps; it does not *activate* anything). This is what keeps Maps benchmark-safe, exactly as decision-trail's "no scorer change" did.
- Additive: multiple active Maps union their members.

### 3.3 Cartographer auto-seed (`maze/cartographer.py` extension)
- After drafting Scent, the Cartographer reads the dbt structure (marts folders/groups/tags via the manifest `DbtLoader` already parses) and drafts one `kind="map"` skeleton per domain-group: members = the Scent domains for that group's models; empty Trails/Decisions/prompts (the human fills those). `source="draft"` (structure-derived), contamination-audited, human-gated before it counts as curated. Deterministic; no LLM; benchmark posture identical to the existing structure-only Scent pre-pass.

### 3.4 Authoring + activation surface (TUI)
- Create/curate a Map (from an auto-seeded skeleton or blank): add/remove Scent + Trail members (pick from existing), edit prompts. Mirror the Trail/decision capture flow; audited human-gated write; git-shareable like team-Scent.
- Activate/deactivate Maps (a picker; multiple active). The active set feeds the retrieval filter. A status indicator shows which Maps are active.

## 4. Non-negotiables

1. **No retrieval-scorer change.** Activation is a member-set *filter*; empty active set (default + all benchmark runs) → retrieval byte-identical to today. Enabling filtering on a benchmark/submission would require a DAB A/B first (parked) — so the benchmark never activates Maps.
2. **Reference, not copy.** Maps store only member IDs; resolved live; dangling ref → soft-miss, never stale content, never an error. (The user's maintainability requirement, satisfied by construction.)
3. **Reuse.** `MazeStore` (`kind="map"`), `document.py`, the contamination audit, team-Scent git-versioning, the Cartographer, and the existing `search_reference_docs`/`search_trails` retrieval — all consumed as-is; the only new retrieval code is the pre-filter.
4. **Auto-seed is deterministic + human-gated + no LLM** — same posture as the structure-only Scent pre-pass; benchmark-safe.
5. **Opt-in / default-off.** No Map is active until a user activates one; nothing changes for a user who never touches Maps.

## 5. Testing

- Model: a `kind="map"` doc round-trips; `scent_members`/`trail_members`/`prompts` parse; a dangling member resolves to a soft-miss (no raise).
- Activation filter: with an active Map, `search_reference_docs` returns only that Map's Scent domains; with the active set empty, it returns exactly today's results (byte-identical — the benchmark guarantee); additive union across two active Maps.
- Auto-seed: a fixture dbt project with `marts/finance/` + `marts/product/` → the Cartographer drafts a Revenue Map + Product Map skeleton whose Scent members are those groups' domains; deterministic; contamination-audited.
- TUI: create/curate a Map, activate it, confirm the agent's grounding is scoped; deactivate → full grounding restored.
- Benchmark isolation: nothing under `eval/`/`mcp/` activates a Map; the Cartographer pre-pass sketches but never activates.

## 6. Out of scope (v1 → later increments)

- **Share/export** a Map as a portable materialized pack (the team-tier install path) — v1 relies on git-shared reference Maps (team-Scent mechanism); a `labrat map export` that materializes pointers is a clean follow-on.
- **Auto-derived Maps** beyond dbt structure (co-occurrence inference from `context_engine`) — v2.
- **Map-references-Map** nesting — v1 uses *additive activation* for cross-domain instead (the user's call: activate Revenue + Product, don't nest).
- Onboarding UX beyond carrying `Suggested Prompts` metadata (surfacing them as clickable starters is a later polish).
- Any retrieval-scorer change / enabling the filter on the benchmark path (needs the parked DAB A/B).

## 7. Suggested v1 build scope (for the plan)

Minimal coherent loop: **(a)** `maze/map.py` doc model + resolution + soft-miss; **(b)** the activation retrieval-filter over both search tools (default-off, additive, byte-identical when empty); **(c)** manual authoring + activation TUI; **(d)** Cartographer auto-seed from dbt structure. (a)+(b) are the mechanic and must ship together; (c) makes it usable; (d) is the differentiator and bridges the dbt work — include if scope allows, else fast-follow.

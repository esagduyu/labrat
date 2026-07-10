# Warren — Design Exploration (RFC, not a locked spec)

**Date:** 2026-07-10 · **Status:** DESIGN EXPLORATION for user review — frames the decision space + a recommended v1; the core-shape decisions (W1–W3) are product-vision calls the user should make before this becomes an implementable spec. Produced autonomously (weekend run) as a starting artifact, deliberately NOT a full spec.
**What Warren is (north-star §8, canonical):** "a domain area of the maze — a bundle of Trails + grounding (Scent) for one domain, packaging suggested prompts" (= Meta **Cookbook**). It is the **aggregation layer** over the now-shipped Scent + Trail: `Scent → Trail → Warren`. The `warren` kind is already reserved in `maze/document.py`/`store.py` (forward-compatible; zero code today).

## Why now / why an RFC

Scent (grounding), Trail (procedures), decision-trail (institutional rules), and team-Scent (git-shared) all shipped this cycle. Warren is the missing top layer — "the aggregation layer" the north-star flags as **Missing** (§8, line 96). But unlike Trail/dbt-CI (which had a clear mechanical shape), Warren's core definition is a **product-vision** call: what a bundle *is*, how it's assembled, and how it's shared/consumed. This RFC lays out the options so the user can decide the shape before a build spec is written.

## The three decisions that define Warren (W1–W3)

### W1 — What IS a Warren doc: a REFERENCE index, or a COPY bundle?
- **(a) Reference/manifest [RECOMMENDED]:** a `kind="warren"` doc is a named index that *references* the Scent domains + Trails belonging to a domain (a list of domain slugs + trail intent-slugs + optional suggested prompts). The underlying Scent/Trail docs stay the single source of truth; the Warren points at them. No duplication; edits to a Trail flow through. Mirrors how team-Scent already treats docs as the artifacts.
- **(b) Copy/bundle:** a Warren physically packages copies of its Scent+Trails into one shareable unit. Self-contained (good for export/install to a machine without the originals), but duplicates content and drifts from source.
- **Recommendation:** (a) for the in-repo/team case (no duplication, live), with an *export-to-bundle* operation (b-flavored) as the share/install mechanism (§ Share below) — reference internally, materialize on export.

### W2 — How is a Warren assembled: MANUAL, or DERIVED?
- **(a) Manual [RECOMMENDED v1]:** the analyst explicitly groups existing domains/Trails into a named Warren ("Revenue Analytics" = domains {orders, refunds}, trails {compute-mau, attribute-revenue}). Deliberate, no false positives — mirrors the explicit-capture choice that worked for decision-trail and Trail promotion.
- **(b) Auto-derived:** infer Warrens from co-occurrence (tables/trails that recur together → a suggested bundle). Higher magic, but speculative and needs the recurrence signal (`context_engine`) — a v2 at most.
- **Recommendation:** (a). Assembly is a deliberate curation act, like promoting a Trail.

### W3 — What is a Warren FOR at consumption time?
Three non-exclusive uses; v1 should pick the primary:
- **(a) Activation/focus [RECOMMENDED primary]:** activating a Warren scopes the agent's grounding to that domain's Scent+Trails (a focused `search_reference_docs`/`search_trails` over just the Warren's members) — "I'm doing revenue work; load the Revenue Warren." A retrieval *filter*, not a scorer change (benchmark-safe — same reasoning that kept decision-trail safe).
- **(b) Share/install:** export a Warren as a portable unit another analyst/team imports (the land-and-expand / team-tier path, §8 line 80). This is the commercial-memo team-scale surface.
- **(c) Onboarding surface:** a Warren's "suggested prompts" seed a new analyst ("start here").
- **Recommendation:** (a) activation as the v1 mechanic (reuses retrieval, benchmark-safe), with (b) export as a thin follow-on and (c) as metadata carried but not yet surfaced.

## Recommended v1 shape (if the user ratifies W1a/W2a/W3a)

- **`maze/warren.py`** — a `WarrenDoc` (`kind="warren"`, `domain`=warren-slug, sections: `Members` (the referenced Scent domains + Trail intent-slugs), `Suggested Prompts`, `Overview`). Authored via a manual "create/edit Warren" TUI action (mirror the Trail/decision capture flow) or a CLI. Contamination-audited + human-authored (`source="human"`); lives in `labrat_maze/warren/` (the reserved kind), git-shareable exactly like team-Scent.
- **Activation** — a TUI action / setting picks an active Warren; the agent's `search_reference_docs`/`search_trails` calls are *filtered* to the Warren's member domains/trails (a pre-filter on the doc set, NOT a scorer change → benchmark path untouched when no Warren is active).
- **Export (thin)** — `labrat warren export <slug>` materializes the referenced docs into a portable dir another repo imports (the b-flavored share op).
- **Reuse:** `MazeStore` (`kind="warren"` already accepted), `document.py` parse/render, the audit guard, team-Scent's git-versioning — all as-is. No retrieval-scorer change, no dependency, no LLM (activation is a deterministic filter).

## Non-negotiables (for the eventual build)
1. **No retrieval-scorer change** — activation is a member-set *filter*; with no active Warren the retrieval path is byte-identical (benchmark-safe, the decision-trail lesson).
2. **Reference-not-copy internally** (W1a) — Warrens point at the live Scent/Trail docs; export materializes.
3. **Human-authored + audited** — Warrens are `source="human"`, contamination-audited on write; git-shareable like team-Scent.
4. **Reuse the kind** — `warren` is already reserved; no store/parser changes.

## Open questions for the user (blocking a build spec)
- **W1/W2/W3** above — ratify the recommended v1 (reference + manual + activation) or steer otherwise.
- **Naming:** the north-star (§ line 310) flags "Warren" itself as *still-open* to confirm/replace. Keep "Warren" or rename the domain-bundle concept?
- **Scope priority:** is the near-term value the *personal* focus mechanic (a), or the *team share/install* path (b, the commercial-memo upsell)? That reorders v1.
- **Relationship to dbt:** should a Warren map 1:1 to a dbt project / dbt group, auto-seeding its members from the dbt semantic models? (A bridge to the shipped dbt integration.)

## Status
This is a design *exploration*, not an implementable spec — no build until the user ratifies the shape (W1–W3) and the open questions. It reuses everything shipped this cycle and needs no new dependency; the primary blocker is product vision, not engineering.

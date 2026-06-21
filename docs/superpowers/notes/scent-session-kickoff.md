# Scent session — kickoff prompt

Paste the block below to open the dedicated clean session that builds the **Scent / grounding layer** (Pillar 3, the North Star knowledge moat). It orients a fresh Claude with full context (memory notes + roadmap + the dirty-date motivating case), sets the build arc, carries the non-negotiable rails, and hands off to the superpowers flow starting with brainstorming — leading with the one real design risk (forward-compatibility with the full Rat Maze).

Authored 2026-06-21 at the end of the GPT-5.5 experiment session, whose conclusion was: **the lever is grounding, not the model or more reasoning machinery** (GPT-5.5 ≈ Sonnet; verifier no-benefit; failures were ungrounded data handling). See `FEATURE_ROADMAP.md` STATUS + `docs/dab-progress-report.md` §Phase 6.

---

```
We're building the Scent / grounding layer — LabRat's Pillar 3 knowledge moat
(the "21%→95%" reference-doc layer, "Claude Code for data scientists" North Star).
This is the highest-leverage roadmap work; a prior session concluded the lever is
*grounding*, not the model or more reasoning machinery.

READ THESE FIRST (full context, no re-derivation needed):
- Memory: project_scent_reference_docs (the locked design), reference_anthropic_self_serve_analytics
  (the 21%→95% blueprint), project_north_star_vision, feedback_use_superpowers_when_building.
- FEATURE_ROADMAP.md: the STATUS block (top-5 priorities + next-session arc) and items
  #26a, #26b, #2, #30. (FEATURE_ROADMAP.md is local/gitignored.)
- docs/superpowers/specs/2026-06-01-labrat-north-star-design.md §8 (the full Rat Maze:
  Scent → Trail → Warren, user+team scopes), §8a + §9 (local), and
  docs/dab-progress-report.md §"Phase 6" — esp. the stockindex dirty-date case, which is
  the poster child: a query that fails only because a `Date` column is dirty mixed-format
  text, fixable for ANY model by a one-line reference-doc Gotcha.

BUILD ARC (substrate first, optimization second):
1. Cheap grounding prompt levers — force-query rule (music_brainz answer-from-memory) +
   a dirty-data/date-parse anti-pattern bullet (stockindex). Ablate each against the
   9-task ADE smoke set + a tiny DAB subset; keep only net-positive.
2. #26a — a `search_reference_docs(query, top_k)` tool: deterministic lexical retrieval
   (same approach as link_schema, no LLM), reading a DUAL store — project-local
   ./labrat_scent/ AND global ~/.labrat/scent/<profile>/ (project wins on conflict).
   Reference-doc format = the Anthropic template (Quick Reference / Dimensions / Key Tables
   w/ grain+joins / Gotchas / Best Practices / Cross-refs). Register in
   build_data_tools_registry() so it reaches agent + MCP + TUI; add a system-prompt
   "consult reference docs first" router line. Ship a template + one worked example.
   Design the store layout to be FORWARD-COMPATIBLE with the full Rat Maze (see PROCESS).
3. #26b — the auto-cartographer: a first-run agent pass that explores a DB and writes a
   CURATED draft (orchestrates profile_dataset + verify_join + link_schema + context_engine
   prioritization), with verified structure stated confidently and business semantics
   flagged low-confidence for a human to own. Plus a maintenance loop: stamp an
   information_schema hash → cheap kickoff diff → regen only changed tables, re-verify
   affected joins, FLAG (never overwrite) human-curated sections.
4. (Optional, after substrate is solid) wrap the cartographer + prompt levers in a
   self-improving /loop: one change/iteration → ablate on a small DAB tuning subset
   (deps_dev_v1, music_brainz, stockindex; n low) → keep/revert → validate gains on a
   held-out subset → loop-until-held-out-dry.

NON-NEGOTIABLE RAILS:
- Benchmark-safe by construction: the *mechanism* ships, the *content* is user-authored.
  The store is EMPTY for DAB/ADE → the tool is a no-op there → zero leakage. NEVER author
  answer-shaped docs for a held-out benchmark; cartographer + loop are STRUCTURE-ONLY /
  GT-firewalled (read schema+samples only, never validate.py/ground_truth.csv).
- Ablate every change; revert net-negative (don't stack on faith).

PROCESS: use the superpowers plugin and follow its ordered steps — START WITH BRAINSTORMING,
then writing-plans, then TDD (full gate: ruff/pyright/pytest), on a feat/scent-reference-docs
branch. I'll stay in the loop and approve merges. Brainstorm agenda for #26a:
  (a) the reference-doc data model + dual-store file layout + lexical retrieval scoring;
  (b) FORWARD-COMPATIBILITY — sketch how this store/scoping extends to the full Rat Maze
      (Scent → Trail/recipes → Warren/domain bundles, scoped user AND team) so the MVP
      layout doesn't need rework when those land. This is the one design risk to settle
      up front; everything else is incremental.

Start by reading the pointers above, then brainstorm the #26a design with me — leading
with the forward-compatibility check.
```

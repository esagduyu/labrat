# Team Scent — committing `labrat_maze/` as shared project memory

Scent is LabRat's grounding layer: short markdown reference docs (per-domain "gotchas",
verified key-table notes, harvested corrections, dbt semantic-layer facts, …) that tools like
`search_reference_docs` retrieve from and the Cartographer pre-pass writes into. It lives in
two layers on disk:

```
<project_root>/labrat_maze/scent/*.md          project layer — commit this
<home>/.labrat/maze/<profile>/scent/*.md       user layer    — do NOT commit this
```

This doc is about the **project layer**. It's plain markdown, deterministic, and grows one
audited section at a time — which makes it a normal, reviewable artifact, not a generated
blob.

## Why commit `labrat_maze/`

Everything a teammate's session learns about your warehouse — "exclude `status='cancelled'`
from revenue sums," "the `orders`→`customers` join fans out 3x, filter first" — either dies
with their session or gets committed and shared. Committing it means:

- **The whole team benefits from one person's correction.** The next teammate (or the next
  agent session) that touches `orders` gets the gotcha for free via `search_reference_docs`,
  instead of re-discovering it the hard way.
- **PR review becomes a third trust layer.** Scent writes already pass through two guards
  before they land on disk:
  1. **The contamination audit** (`maze/scent_audit.py`) — fail-loud, runs on every write path,
     blocks anything answer-shaped or benchmark-leaked from ever reaching a file.
  2. **The human harvest-review gate** — a teammate explicitly approves each drafted section in
     the review modal before it's applied; nothing is auto-promoted.
  3. **Git review** — once committed, the section is an ordinary diff hunk. A reviewer reads it
     the same way they'd read a code change: does this claim actually hold, is the wording
     right, should it live in this domain.

  None of the first two layers substitute for the third — they catch structural/contamination
  problems and get a single approver's sign-off, but a *wrong-but-clean* claim ("orders are
  keyed by `order_id`" when they're actually keyed by `id`) sails through both. Git review is
  where a second pair of eyes catches that, same as it would in code.
- **It's just markdown.** No special tooling is required to read, edit, or revert it — `git
  log`, `git blame`, and `git diff` all work exactly as they do on any other doc in the repo.

## What NOT to commit: the user layer

`~/.labrat/**` (including `~/.labrat/maze/<profile>/scent/*.md`) is **machine-local and
regenerable** — never commit it, and it isn't inside the project repo in the first place so
there's nothing to accidentally `git add`. It holds:

- The Cartographer's structure-only first-connect pre-pass (`maze/cartographer.py`) — one
  teammate's local `~/.labrat` tree can differ from another's (different profile name, different
  connection at first-connect time), and regenerating it is cheap (idempotent, deterministic).
- Anything else scoped to "this analyst, this machine."

If a user-layer fact is actually a durable, team-worth-sharing fact (a real gotcha, a real
verified table note), the harvest workflow below is how it gets promoted into the project layer
where it belongs. Until then, leave it local.

## The workflow: harvest → review → apply → commit

1. **Harvest.** The TUI's session harvester (`memory/harvest.py::SessionHarvester`, wired at
   `screens/harvest_controller.py`) clusters correction memories (draft-vs-executed SQL edits,
   chat corrections) captured during a session and drafts them into `Section`s tagged
   `source="harvested"` (`maze/harvest.py::draft_harvested_sections`). This is opt-in
   (`Profile.harvest_opt_in`, fail-closed by default) and triggers on an explicit action
   (Ctrl+Shift+H) or a thread-switch confirm — never silently.
2. **Review modal.** `HarvestReviewScreen` shows each drafted section (domain, heading, body
   preview) with a per-row approve/reject toggle. Nothing is written until you explicitly apply.
3. **Apply.** Approving and applying calls `maze/harvest.py::apply_approved_sections`, which:
   - loads **only the project layer** (never the merged view — so a user-layer Cartographer
     section can never get frozen into a stale project copy),
   - dedups against existing project-layer section bodies (re-approving the same bullet twice
     is a no-op),
   - stamps `git_sha` on newly-appended sections when the project root is a git repo
     (`maze/gitmeta.py::current_git_sha` — `git -C <root> rev-parse --short HEAD`; no repo / no
     git / any subprocess failure → `None`, and the write is byte-identical to the no-git-root
     path since the Meta renderer omits `None` fields),
   - runs the contamination audit **before** writing (fail-loud: a tripped audit blocks that
     domain's write and reopens the modal with the verdict shown — atomicity is per domain doc,
     so in a multi-domain apply, domains already written before the tripped one stay on disk),
   - writes the doc via `MazeStore.write_doc(..., scope="project")`.

   The same `git_sha` stamp applies to dbt semantic-layer ingestion
   (`maze/semantic_ingest.py::ingest_dbt_semantics`) — every `semantic_layer`-tagged section it
   writes gets stamped the same way, before that write path's own per-domain audit.

4. **`git diff` shows the new section with full provenance.** A freshly-applied harvested
   section looks like this in `labrat_maze/scent/orders.md` (real output, from an actual applied
   section):

   ```markdown
   ## Gotchas
   **Source:** harvested
   **Meta:** generated_at=2026-07-08T12:00:00Z; git_sha=a1b2c3d

   - status='cancelled' rows should be excluded from revenue sums
   ```

   `**Source:**` names the provenance tier (`harvested`, `semantic_layer`, `verified`,
   `lineage`, `draft`, `human`); `**Meta:**` carries whichever of `generated_at` / `schema_hash`
   / `model_id` / `git_sha` are non-`None` for that section (the renderer omits absent fields, so
   a hand-written `human` section shows no Meta line at all). The `git_sha` is exactly what
   lands in the diff — a reviewer can `git show <sha>` to see the repo state the section was
   authored against.

5. **Commit / PR.** From here it's an ordinary change: `git add labrat_maze/`, commit, push,
   open a PR. Review it like code — see "Why commit" above for what the diff review is actually
   catching.

## Merge guidance

- **Section-per-block.** Each `## heading` + its `**Source:**`/`**Meta:**` line + body is one
  contiguous diff hunk. But note: applies *append* sections at the end of the domain file, so two
  branches that each add a section to the *same* domain doc will usually both touch the same
  end-of-file region — expect git to flag a **trivial add/add conflict** on that hunk rather than
  auto-merging. Resolution is mechanical: keep both blocks (order doesn't matter — sections are
  independent), delete the conflict markers, commit. Branches touching *different* domain docs
  merge cleanly with no interaction.
- **Concurrent applies of the same learning: trim the duplicate at merge time.** If two teammates
  independently harvest and apply the *same* correction on separate branches, the merge
  resolution above would land two near-identical `## Gotchas` blocks with the same body text —
  keep one and drop the other while you're already resolving the hunk. If a literal duplicate
  does slip through into a domain that exists in **both** the user and project layers,
  `MazeStore.docs()`'s merge-at-read view (`store.py::_merge_domain`) dedups sections by body
  (stripped) across the two layers, so readers self-heal; but for a **project-only** domain (the
  usual case for harvested gotchas) the merged view has nothing to dedup against and both copies
  surface — the on-disk trim is the real fix, not optional cleanup.
- **True conflicts resolve like any doc.** If two branches edit the *same* section's body
  differently, that's a real git conflict — git will flag it as an ordinary merge conflict on
  that hunk, and you resolve it the way you'd resolve any prose conflict: read both versions,
  pick or combine, commit. Scent doesn't add any special conflict machinery on top of git's own.

  > **Caveat:** the merged *view* (`store.docs()`, what `search_reference_docs` and the status
  > CLI below actually read) dedups by section **body** only. If the user layer and the project
  > layer both end up with a section that has the *same body but different metadata* (e.g. a
  > different `schema_hash` or `git_sha` stamp), the merged view keeps whichever copy it sees
  > first — the **user-layer copy's metadata wins** and the project-layer copy's metadata is
  > silently dropped from the merged view (the on-disk project file itself is untouched). The
  > status CLI below makes this visible: watch for a domain reporting `scope: merged` where the
  > `tiers`/freshness breakdown looks off from what you'd expect from the project file alone.

## The status CLI: inventory, tiers, freshness, drift

`maze/status.py::build_status` + `render_status` produce a read-only report over a project's
Scent store: per domain, which scope(s) contributed sections, a count by provenance tier, the
highest-trust source present (`maze/provenance.py::best_source`), a fresh/stale/unknown
breakdown of each section's `schema_hash` against a live catalog fingerprint, and the two
sidecar drift signals (`.schema_fingerprint` for the Cartographer pre-pass,
`.manifest_fingerprint` for dbt semantic ingestion). It never writes anything — no store writes,
no side effects beyond stdout.

```bash
uv run python -m labrat.maze.print_status [--profile <p>] [--db <path.duckdb>] [--project-root <dir>]
```

`--profile` selects the user-layer profile (default `"default"`); `--db` connects a read-only
DuckDB file to compute the live fingerprint (omit it and every section reports `unknown`
freshness — no catalog to compare against); `--project-root` overrides the project root
(default: `LABRAT_MAZE_DIR` env var, else cwd — the same rule `MazeStore.from_env` uses).
Exits 2 on a bad `--db` path or connection failure, 0 otherwise.

### Sample output

Generated by seeding a fixture store (two domains: `orders` with a user-layer `verified`
section stamped with the *current* warehouse fingerprint plus a project-layer `harvested`
gotcha, and `customers` with a project-layer `semantic_layer` section stamped with a
deliberately stale fingerprint) and running:

```bash
HOME=<fixture-home> uv run python -m labrat.maze.print_status \
  --profile team \
  --project-root <fixture-proj> \
  --db <fixture-warehouse.duckdb>
```

```
fingerprint: 8e3c8d6c… | scent sidecar: n/a | manifest sidecar: no

domain     scope    sections  best            fresh/stale/unknown  tiers
customers  project  1         semantic_layer  0/1/0                semantic_layer=1
orders     merged   2         verified        1/0/1                harvested=1, verified=1
```

Reading it: `customers` is fully project-scoped, its one `semantic_layer` section is `stale`
(its stamped `schema_hash` no longer matches the live catalog — time to re-run dbt ingestion or
`--force` a re-ingest). `orders` is `merged` (both layers contributed: the user-layer `verified`
note plus the project-layer `harvested` gotcha), `best` is `verified` (the highest-trust tier
present), and its `fresh/stale/unknown` split is `1/0/1` — the `verified` section is stamped and
matches the live fingerprint (fresh), the `harvested` gotcha carries no `schema_hash` at all
(harvested sections aren't schema-derived facts, so they're always `unknown`, not stale). `scent
sidecar: n/a` because this fixture never wrote a `.schema_fingerprint` sidecar for the user
layer; `manifest sidecar: no` because no dbt manifest was ingested.

## See also

- `docs/superpowers/specs/2026-07-10-team-scent-design.md` — the design spec this doc
  implements (D1–D4).
- `docs/superpowers/specs/2026-07-09-scent-read-model-v2-design.md` — the merge-at-read view
  (`MazeStore.docs()`, body-dedup) this doc's merge-conflict guidance depends on.
- `src/labrat/maze/status.py`, `src/labrat/maze/print_status.py` — the status surface.
- `src/labrat/maze/gitmeta.py` — the `git_sha` stamping helper.
- `src/labrat/maze/harvest.py`, `src/labrat/screens/harvest_review.py` — the harvest → review →
  apply pipeline.

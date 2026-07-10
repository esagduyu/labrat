# dbt-CI pairing — a staleness gate for committed Scent

`labrat scent check` is a read-only CI gate: it fails a pull request when a user's dbt models
changed but the paired Scent (`labrat_maze/scent/`) wasn't refreshed in the same commit. It
answers one question, offline, with no live warehouse: **does the committed Scent still match
the committed dbt project?**

This doc is the setup + fix-a-failure walkthrough. For what Scent is and why you commit it in
the first place, see [`docs/team-scent.md`](team-scent.md) — dbt-CI pairing is what makes that
free, git-versioned Team Scent actually *operate* at team scale (see "Positioning" below).

## 1. Colocate: commit `labrat_maze/` beside your dbt project

The check compares two things that both have to be in the repo:

- **The dbt project's compiled manifest** — `<project>/target/manifest.json`, produced by
  `dbt parse` (or `dbt compile`/`dbt run`). Not usually committed; CI regenerates it fresh on
  every run.
- **The committed Scent** — `labrat_maze/scent/*.md`, plus its fingerprint sidecars, committed
  to the repo alongside the dbt project. If you haven't done this yet, see
  [`docs/team-scent.md`](team-scent.md) for the full colocate/harvest/review/commit workflow —
  the short version is: `labrat_maze/` (project layer) is meant to be `git add`-ed, `~/.labrat/**`
  (user layer, machine-local) is not.

Nothing here writes to your repo. `labrat scent check` never touches disk.

## 2. Wire the check: two CI steps

Once both pieces exist, the CI job is two commands:

```yaml
- name: dbt parse
  run: dbt parse

- name: labrat scent check
  run: labrat scent check
```

`dbt parse` compiles the project without hitting a warehouse, which is what produces
`target/manifest.json`. `labrat scent check` then reads that manifest plus the committed
`labrat_maze/scent/` directory and exits `0` (fresh) or `1` (stale). Useful flags:

- `--dbt-project PATH` — defaults to the active profile's `dbt_project_path`, or pass it
  explicitly in CI.
- `--warn-only` — report drift but always exit `0` (use while rolling the gate out before making
  it required).
- `--skip-if-no-manifest` — exit `0` instead of `1` if `target/manifest.json` is missing (useful
  for a job that also runs on non-dbt paths).
- `--format json` — machine-readable `CiCheckResult` for a custom reporting step.

Scaffold a starter workflow with the CLI instead of hand-writing it:

```bash
labrat scent init-ci                    # writes .github/workflows/labrat-scent.yml
labrat scent init-ci --path <custom>    # writes elsewhere
```

`init-ci` never overwrites an existing file at `--path` — if one's already there, it exits
non-zero and leaves it untouched. `--platform` only supports `github` in v1; the check itself
(`labrat scent check`) is platform-agnostic, so any other CI wires the same two steps by hand.
The generated starter looks like this:

```yaml
name: labrat-scent

on:
  pull_request:
    paths:
      - "models/**"
      - "labrat_maze/**"

jobs:
  scent-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dbt + labrat
        run: pip install dbt-core labrat

      - name: dbt parse
        run: dbt parse

      - name: labrat scent check
        run: labrat scent check
```

## 3. What a stale-Scent PR failure looks like

A model or semantic-layer change lands without a matching `labrat scent ingest`, so the check
fails the PR:

```
$ labrat scent check --dbt-project .
scent check: STALE (1 stale domain(s))
  - orders: semantic_drift
fix: labrat scent ingest --dbt-project .
```

Two distinct reasons can appear in `stale`, per domain:

- **`semantic_drift`** — the dbt project's `semantic_models`/`metrics` definitions changed (a
  measure expression, a new metric, …) since the committed `.manifest_fingerprint` sidecar was
  last written.
- **`schema_drift`** — the underlying table/column shape changed since a committed
  `semantic_layer` Scent section was stamped with its `schema_hash`.

Either way the process is the same: the check found a *fingerprint mismatch* between what's
committed in `labrat_maze/scent/` and what the current dbt project would produce — it doesn't
try to infer intent from the diff, it just proves the two are out of sync.

Two special cases exit non-zero for a different reason and print their own guidance:

- No `target/manifest.json` — the CI job forgot the `dbt parse` step, or ran it against the
  wrong path. Fix the workflow, not the Scent.
- No committed Scent at all for this project — this is **not** treated as stale (there's nothing
  to have drifted from), so this one actually exits `0` with a note. If you expected an ingested
  domain and don't see it, you likely haven't run `labrat scent ingest` yet at all.

## 4. Fix: `labrat scent ingest`

The fix command the check prints is runnable locally or in a follow-up CI/bot job — no need to
open the TUI:

```bash
labrat scent ingest --dbt-project .
```

This is the one write path in the `scent` command group: it recomputes the semantic sections
from the current `manifest.json`, replaces each affected domain's `semantic_layer` section,
refreshes the `.manifest_fingerprint` sidecar, and re-stamps `schema_hash`/`git_sha` — through
the same contamination-audited write path the Cartographer and the TUI's interactive ingest use.
Then, as with any other Scent change:

1. **Review the diff.** `git diff labrat_maze/` — it's plain markdown, so you're reading an
   ordinary prose/metadata diff, not a generated blob. Confirm the new section content is
   accurate, not just "it wrote something."
2. **Commit it.** `git add labrat_maze/`, commit, push. The PR now carries both the dbt change
   and its paired Scent update in the same diff — which is the whole point of the gate.

## Positioning

Git-versioned Team Scent (colocating `labrat_maze/` in the repo, reviewed via ordinary PR
review — see [`docs/team-scent.md`](team-scent.md)) is free and stays free: it's the adoption
wedge, the same way a shared Figma file is the wedge for Figma. dbt-CI pairing is the
**paid, team-scale complement**: it's what makes that free colocation *operate* automatically at
team scale — catching staleness at the PR boundary instead of relying on every contributor to
remember to re-ingest by hand.

## Non-negotiables (what shipped)

- **Read-only gate.** `labrat scent check` and the `maze/ci.py` logic underneath it never call
  `write_doc`, never write a sidecar, never create a directory. The only write path is the
  separate, explicit `labrat scent ingest`, which you run deliberately.
- **Offline.** No live warehouse connection — `target/manifest.json` (from `dbt parse`) is
  sufficient. The check never opens a DB connection.
- **Fingerprint-consistency, not diff heuristics.** The check recomputes fingerprints from the
  *committed* dbt project and compares them to what the *committed* Scent records — it doesn't
  try to parse the PR's git diff to guess which models changed (that approach is gameable and
  false-positives on non-semantic edits).

## See also

- [`docs/team-scent.md`](team-scent.md) — what Scent is, the colocate/harvest/review/commit
  workflow, and the status CLI.
- `src/labrat/maze/ci.py` — `check_scent_freshness`, the pure read-only check.
- `src/labrat/cli.py` — `labrat scent check` / `labrat scent ingest` / `labrat scent init-ci`.
- `docs/superpowers/specs/2026-07-10-dbt-ci-pairing-design.md` — the design spec this doc
  implements.

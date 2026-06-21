# Scent reference-doc layer — `search_reference_docs` (FEATURE_ROADMAP #26a)

> **Status:** Design approved 2026-06-21. This is the **consume** half of the Scent layer
> (Pillar 3 / the Rat Maze) — the thin, shippable first slice. The **generate + maintain**
> half is #26b (auto-cartographer), designed separately and built after this lands.
>
> **Branch:** `feat/scent-reference-docs`. **Process:** superpowers — this spec → `writing-plans`
> → TDD → verification → review. Full gate every commit: `ruff format` → `ruff check` → `pyright` → `pytest`.

## 1. Why

Anthropic's self-service-analytics data team measured **<21% accuracy without a skills/reference-doc
layer, >95% with it**. That reference-doc layer is LabRat's **Scent** (north-star design §8/§8a;
`FEATURE_ROADMAP.md` #26). The lever for LabRat is **grounding, not the model or more reasoning
machinery** (DAB Phase 6 conclusion: GPT‑5.5 ≈ Sonnet; verifier no-benefit). The poster child is the
DAB stockindex dirty-date case — a query that fails for *any* model only because a `Date` column is
dirty mixed-format text, fixable by a one-line reference-doc **Gotcha**.

#26a ships the **mechanism** for that: a deterministic retrieval tool the agent consults before it
plans, reading user-authored reference docs. The **content** is user-authored, so the store is empty
on held-out benchmarks → the tool is a no-op there → zero leakage by construction.

## 2. Scope

**In scope (#26a):**
- A reference-doc format (the Anthropic template) + a dual on-disk store.
- A `search_reference_docs(question, top_k)` tool — deterministic, no-LLM, section-level lexical
  retrieval; the same approach as `link_schema`.
- Registration in `build_data_tools_registry()` so it reaches **labrat-agent + MCP/claude-mcp + TUI**.
- A system-prompt router line ("consult reference docs first").
- A shipped authoring template + one worked example.

**Explicitly out of scope (reserved by the design, not built here):**
- #26b auto-cartographer (Scent *generation* + drift/maintenance loop).
- The team-scope store layer (the precedence model leaves room for it).
- Provenance-footer rendering (#29).
- `trail` / `warren` document kinds (the `kind:` discriminator reserves them).
- dbt semantic-layer ingestion.
- Schema-aware scoring boost (pure lexical for now; `tables:` frontmatter reserved).
- The cheap grounding **prompt levers** (force-query rule, dirty-date anti-pattern bullet) — those
  are arc-step 1, ablated independently of this tool.

## 3. Forward-compatibility (the one up-front design risk — settled)

The on-disk layout and the document/retrieval contract are the expensive-to-migrate surfaces. They
are designed so Trail, Warren, and a team scope are **additive, never migrations**:

1. **One Maze namespace, not sibling dirs.** A `labrat_maze/` root with a `scent/` subdir now;
   `trail/`, `warren/` become siblings later. Avoids littering the repo root with three top-level dirs
   and keeps the project and user stores structurally identical.
2. **Sources as an ordered precedence list**, not two hardcoded paths. A future **team** layer is a
   third entry inserted into the list — no code reshape.
3. **A `kind:` discriminator** in every doc's frontmatter (`scent` now). Trails/Warrens live in the
   same store, read by the same engine filtered by kind.
4. **Section-level retrieval unit.** Token-cheap; returns the precise Gotcha. Trails can retrieve
   whole-doc later without changing the store.

The code package `src/labrat/maze/` mirrors the on-disk namespace and is kind-agnostic, so #26b and
later kinds extend it rather than replace it.

## 4. Architecture / components

New package **`src/labrat/maze/`** (the code mirror of the `labrat_maze/` namespace):

| File | Responsibility |
|---|---|
| `maze/document.py` | `ScentDoc` + `Section` data model; the markdown parser (frontmatter via PyYAML + H2 section split). Kind-agnostic. |
| `maze/store.py` | `MazeStore(project_root, home, profile)`: resolve ordered source layers, discover `*.md` under `labrat_maze/<kind>/`, parse, apply precedence/dedup. Single entry point `docs(kind="scent") -> list[ScentDoc]`. |
| `maze/_lexical.py` | Tokenizer / stemmer / stopwords **extracted from `link_schema.py`**, shared by both tools. |
| `agent/tools/search_reference_docs.py` | The `Tool` subclass: score sections, group by doc, build output. Mirrors `link_schema.py`'s shape. |

`link_schema.py` is refactored to import the shared `_lexical` helpers — **no behavior change**
(its existing tests must stay green; this is a pure extract-and-reuse).

Registered once in `agent/data_tools.py::build_data_tools_registry()` → auto-propagates to the
MCP server, the labrat-agent driver, and the TUI.

## 5. Data model

A reference doc = YAML frontmatter + a markdown body in the Anthropic template:

```markdown
---
kind: scent                 # required — discriminator; reserves trail/warren
domain: ecommerce_sales     # required — human label + dedup key
tables: [orders, customers] # optional — RESERVED, not scored in #26a
confidence: verified        # optional — verified|draft; #26b authors draft for business semantics
provenance: {...}           # optional — RESERVED for the #29 provenance footer
---
## Quick Reference
Business context, grain, hygiene filters.

## Dimensions
How key concepts encode across tables.

## Key Tables
Grain, scope/exclusions, join keys, usage triggers.

## Gotchas
Wrong-answer modes a senior analyst would warn about.

## Best Practices
## Cross-References
```

Rules:
- `scope` is **not** a frontmatter field — it is derived from which store layer the file came from.
- Frontmatter parsed with PyYAML (already a direct dependency, v6). Malformed or absent frontmatter →
  the doc still loads body-only; never crashes the tool.
- The parser splits the body on `##` (H2) headings. The text before the first H2 is a preamble
  section. **Non-template sections are allowed** and scored generically — the template is a convention,
  not a schema the parser enforces.

Data model (Pydantic):

```python
class Section(BaseModel):
    heading: str            # "" for the preamble
    body: str

class ScentDoc(BaseModel):
    domain: str
    kind: str               # "scent"
    tables: list[str] = []
    confidence: str | None = None
    scope: str              # "project" | "user" — set by the store, not the file
    sections: list[Section]
    def quick_reference(self) -> Section | None: ...   # the "Quick Reference" section if present
```

## 6. Store & precedence

Ordered source layers, **lowest → highest precedence**:

1. **user-global** — `~/.labrat/maze/<profile>/scent/`  (`<profile>` from `ctx.profile_name`)
2. **project** — `<root>/labrat_maze/scent/`  — **wins on conflict**

- `<root>` = `$LABRAT_MAZE_DIR` if set, else `os.getcwd()`. The env override matters for MCP hosts:
  the claude-mcp sandbox runs in an isolated scratch cwd, so the project store resolves to nothing →
  correctly empty on benchmarks. Real product use sets the cwd (or the env var) to the project dir.
- Discovery: `*.md` files directly under each `scent/` dir. Missing dir → empty list (no error).
- **Dedup by `domain`** (falling back to filename stem if `domain` frontmatter is absent): if the same
  domain exists in both layers, the **project** doc wins; the user-global one is shadowed.
- A future **team** layer slots in as a third entry between user and project; the resolver iterates a
  list, so adding it is data, not a rewrite.

`MazeStore` takes `project_root` / `home` / `profile` as explicit constructor args so tests inject
temp dirs; the tool builds them from `os.getcwd()` / `Path.home()` / `ctx.profile_name` at execute
time. Filesystem IO at execute time is acceptable (precedent: `load_file`).

## 7. Retrieval engine

Given the question and `top_k`:

1. `MazeStore(...).docs(kind="scent")` → deduped, parsed docs.
2. Tokenize the question with the shared `_lexical` helpers (lowercase → alnum tokens → drop
   stopwords + tokens < 3 chars → stem).
3. Score each **section**:
   `score = 2·|q_stems ∩ heading_stems| + 1·|q_stems ∩ body_stems|`
   where `heading_stems` = stems of `domain` + section heading; `body_stems` = stems of the section body.
4. Keep sections with `score > 0`. Sort by `(-score, domain, section_order)`. Take the top `top_k`
   **matched sections** (the cap is on matched sections, total, across docs).
5. **Group the survivors by doc.** For each doc that contributed a hit, include its **Quick Reference**
   section once (the prepend-context decision), even if the QR itself didn't match — so a returned
   Gotcha is never decontextualized. (If the QR *was* the hit, don't duplicate it.)
6. **No matches, or empty store → `results: []`.** This is the deliberate divergence from
   `link_schema`, which falls back to returning *all* tables. Here, returning nothing is the
   benchmark-safety guarantee and the correct product behavior (no docs → say nothing).

## 8. Tool I/O contract

```
Tool name: search_reference_docs

Input:
  question: str           # the natural-language question to ground
  top_k: int = 5          # max matched sections to return
  # no `database` field — retrieval is over docs, not a catalog

Output:
  question: str
  results: list[DocResult]
    DocResult:
      domain: str
      quick_reference: str | None     # the QR section body, if the doc has one
      sections: list[SectionMatch]
        SectionMatch:
          heading: str
          body: str
          score: float
          matched_terms: list[str]    # human-readable, like link_schema
```

Description (agent-facing), in the spirit of `link_schema`'s:

> "Search the curated reference docs for grounding relevant to the question — metric definitions,
> join keys, table grain, and known data-quality gotchas for this warehouse. Call this FIRST, before
> profiling or writing SQL. Returns nothing if no reference docs are configured."

## 9. System-prompt router

Edit `agent/prompts/system_base.md` — insert a new **Workflow step 1** and renumber:

> **1. Consult reference docs.** Call `search_reference_docs` with the user's question to pull any
> curated grounding for this warehouse — metric definitions, join keys, and known data-quality
> gotchas. Treat returned **Gotchas** as authoritative. If nothing is returned, just proceed.

Also add a one-line entry in the **Tool Usage** list. The wording is inert when `results` is empty,
so it costs one no-op tool call on an unconfigured warehouse and nothing semantically.

## 10. Benchmark safety (by construction)

- Empty store → `results: []` → no-op. An explicit test asserts this.
- DAB/ADE runs have no `labrat_maze/` dir and an empty `~/.labrat/maze/<profile>/scent/` → zero
  content → **zero leakage** (the contamination smell removed in the DAB cleanup).
- The shipped worked example targets the **ecommerce sample fixture**
  (`tests/fixtures/sample_dbs/ecommerce.duckdb`), a non-benchmark domain. It is **not auto-loaded**
  anywhere — tests copy it into a temp store. Never author benchmark-answer-shaped docs.
- Any future benchmark use of Scent must be ablated against the 9-task ADE smoke set.

## 11. Shipped content

- `docs/scent/TEMPLATE.md` — the authoring template (the §5 section headers + inline guidance on
  writing each section "for retrieval by an LLM": routing triggers, not recipes).
- `docs/scent/examples/ecommerce_sales.md` — one worked example against the ecommerce fixture,
  demonstrating a real Gotcha and Key-Tables join keys.

## 12. Testing plan (TDD)

Write tests first, one component at a time:

- **document** — frontmatter parse (valid / malformed / absent → graceful body-only); H2 section split;
  preamble handling; non-template sections retained.
- **store** — both layers resolved; **project wins** on domain conflict; missing dirs → empty;
  `<profile>` segment honored; `LABRAT_MAZE_DIR` override; dedup-by-domain.
- **_lexical** — extracted helpers behave identically; **`link_schema`'s existing tests stay green**
  (regression guard on the refactor).
- **scoring** — ranking order, heading-weight (2×), stemming + stopword behavior, `top_k` cap,
  no-match → empty, QR-prepend (present once per hit doc, not duplicated when QR is itself the hit).
- **tool** — dispatch through `ToolRegistry`; output shape; matched_terms; **empty-store no-op**
  (the benchmark-safety test).
- **registry** — `search_reference_docs` present in `build_data_tools_registry()` (so MCP + agent +
  TUI all get it); appears in `to_anthropic_schemas()`.

Gate every commit: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.

## 13. Decisions settled during brainstorming

- **Store layout:** Maze-namespaced, symmetric (`labrat_maze/scent/` + `~/.labrat/maze/<profile>/scent/`).
- **Retrieval unit:** section-level, with the doc's Quick Reference prepended per hit doc.
- **Scoring:** pure lexical, `link_schema` mechanics (`2·heading + body`); no catalog dependency.
- **Code package:** `src/labrat/maze/` (mirrors the namespace; #26b and Trail/Warren extend it).
- **Project-root resolution:** `$LABRAT_MAZE_DIR` else `os.getcwd()`.
- **Empty/no-match:** return `[]` (divergence from `link_schema`'s fall-back-to-all) — the safety rail.

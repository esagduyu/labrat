# PanCancer Atlas Cartographer / Semantics Autopsy (2026-07-04)

Forensic turn-by-turn analysis of the two pancancer_atlas DAB failures from the
Sonnet-5 5-dataset ablation (`runs/dab/s5-abl/`, claude-mcp driver, n=3).

| Query | baseline (det. Cartographer + hints) | +LLM-semantics (M2) | Failure mode |
|-------|------|------|------|
| pancancer_atlas:1 | **0/3 FAIL** | **0/3 FAIL** | (A) persistent grounding gap — *not* an M2 regression |
| pancancer_atlas:2 | 3/3 PASS | 3/3 PASS | control |
| pancancer_atlas:3 | **3/3 PASS** | **1/3 PASS** | (B) M2 regression |

**Headline finding (structural, applies to BOTH failures):** the Cartographer pre-pass
**never profiled the `clinical_database` (Postgres) table `clinical_info`** — the exact table
on which both failures turn. `build_dab_task_env` (`env.py:74-118`) puts DuckDB connections
into `ctx.connections` but routes Postgres/SQLite into a separate `attachable` list
("*Postgres / MongoDB entries are dropped silently until adapters land*"). `_run_cartographer`
(`suite.py:389`) and `generate_scent` (`cartographer.py:427`) iterate **`ctx.connections`**,
so the Scent covers only `molecular_database` (Mutation_Data, RNASeq_Expression). Every
`search_reference_docs` result in all 12 traces returns `"domain":"molecular_database"` and
**nothing about `clinical_info`'s columns**. The code-vs-name machinery that was *built to fix
exactly failure (A)* never gets a chance to run on the table that needs it.

---

## Failure Mode A — pancancer_atlas:1 (persistent, both arms, 0/3)

### Question / ground truth / validator
> "For LGG patients, compute the average log10-transformed expression of the IGF2 gene across
> different **histology types**. Only include patients with valid IGF2 expression values and
> histology annotations that are **not enclosed in square brackets**. Report … at least four
> decimal places."

Ground truth (`ground_truth.csv`) keys are **ICD-O-3 morphology codes**, not names:

```
9382/3, 2.713571305193452     9400/3, 2.601...   9401/3, 2.558...
9450/3, 2.696...              9451/3, 2.582...
```

`validate.py` does `llm_output.find(hist_code)` for each of the 5 codes, then looks 10 chars
ahead for a decimal matching the GT (4dp). **First miss → fail; the very first code checked is
`9382/3`.** Hence the identical reason across all 6 trials: *"Missing histology type: 9382/3"*.

### Turn-by-turn (baseline trial0, representative — all 6 trials identical in the decisive step)
- **turn 0** `search_reference_docs("LGG histology IGF2 expression clinical join …")` → returns
  **only `molecular_database`** Key Tables + Dimensions. Silent on `clinical_info`.
- **turn 7** `run_sql` schema probe returns the histology columns:
  `icd_o_3_histology`, `histological_type`, `histological_type_other`, `neoplasm_histologic_grade`.
  **The code column `icd_o_3_histology` is right there in the result.**
- **turn 13** `SELECT DISTINCT histological_type … WHERE Patient_description ILIKE '%Lower Grade
  Glioma%'` → `Oligoastrocytoma`, `Astrocytoma`, `Oligodendroglioma` — **3 display names**.
- **turn 17** `CREATE TEMP TABLE lgg_hist AS SELECT … histological_type … WHERE … histological_type
  NOT LIKE '[%]'` — correct square-bracket filter, **but on the name column**.
- **turn 19 (decisive)**:
  ```sql
  SELECT h.histological_type, COUNT(*) AS n_samples,
         AVG(log10(r.normalized_count + 1)) AS avg_log10_igf2
  FROM lgg_hist h JOIN RNASeq_Expression r ON r.ParticipantBarcode = h.barcode
  WHERE r.Symbol = 'IGF2' AND r.normalized_count IS NOT NULL
  GROUP BY h.histological_type ORDER BY h.histological_type
  -- → Astrocytoma 2.5712…, Oligoastrocytoma 2.713571305193452, Oligodendroglioma 2.682…
  ```

### Root cause (pinned): code-vs-name column selection, GROUP BY the display-name column
The GT breaks the cohort into **5 ICD-O codes**; the name column `histological_type` has only
**3 distinct values** (`Astrocytoma` covers codes 9400/3 + 9401/3; `Oligodendroglioma` covers
9450/3 + 9451/3; `Oligoastrocytoma` = 9382/3). **Grouping by the name column can never
reproduce the 5-way GT breakdown** — the codes are strictly finer-grained. Confirmed
mechanically: all 6 trials contain the string `icd_o_3_histology` (seen in schema results) yet
**every final aggregation groups by `histological_type`**.

The numbers the agent *did* compute are partly correct — baseline's `Oligoastrocytoma` avg =
`2.713571305193452`, byte-identical to GT's `9382/3` value. **The arithmetic is right; only the
emitted label is a name where the validator demands a code.** This is a pure grounding/emission
failure, not a compute error.

### Why the Cartographer didn't help (it stayed silent, by construction)
- `clinical_info` was never profiled → **no** Dimensions, Key Tables, or ROLE claim for it.
- The `ROLE <t>.<code_col> CODES <t>.<name_col>` mechanism (`semantic_claims.py`) is *designed
  for exactly this*. Manually checking it against the schema: `icd_o_3_histology` values (e.g.
  `9382/3`, and bracketed `[Not Available]`) match `_CODE_SHAPE_RE`
  (`^\[.*\]$|^(?=.*\d)[A-Za-z0-9/\-._]{1,12}$`); `histological_type` values (`Oligodendroglioma`)
  have no digit and no brackets → `name_score ≈ 0`. So `verify_role_claim` **would return True**
  and emit *"group/filter by the code column when the question asks for codes."* **The mechanism
  is sound; it simply never ran on the relevant table** because (1) clinical wasn't in
  `connections`, and (2) on DAB, ROLE claims are LLM-authored and the baseline arm is
  `with_semantics=False`.

---

## Failure Mode B — pancancer_atlas:3 (M2 regression, 3/3 → 1/3)

### Question / GT / validator
> "Calculate the **chi-square statistic** … association between histological types and presence
> of **CDH1** mutations in **female BRCA** patients … **excluding categories with marginal totals
> ≤ 10** … only … known histological types … **only reliable mutation entries**."

GT = `305.1239198007461`; validator accepts any number rounding to `305.12`, `305.1`, or `305`.

### The passing baseline pattern (all 3 baseline trials + M2 trial0)
Cohort = **all** BRCA-female patients with a non-empty `histological_type`; `Mutated` = has a
CDH1 `FILTER='PASS'` mutation (LEFT JOIN), everyone else `Not Mutated`; exclude row-marginal ≤10;
chi-square over the 5×2 table → **305.1239**. Baseline trial0 computes it with a **pivot**:
`SUM(mutated) AS n_mut, SUM(1-mutated) AS n_notmut` per category — so every category carries both
cells as explicit columns and **zero cells are structurally present**. M2 trial0 emits the same
contingency table and hand-computes 305.1239 → PASS.

### M2 trial1 (FAIL): dropped zero-cell — a chi-square *shape* bug, NOT semantics content
Final chi SQL groups into **rows** `GROUP BY (histological_type, cdh1_status)`:
```sql
cell AS (SELECT j.histological_type, j.cdh1_status, COUNT(*) AS obs FROM joined j
         JOIN kept k ON … GROUP BY 1,2)
```
`Mucinous Carcinoma` has **0** CDH1-mutated patients (17 total), so the
`(Mucinous, Mutated)` cell **produces no row** and is silently omitted from the sum.
Result: **`303.5346846732673`** (n=1059). The missing term is exactly
`(0 − 17·99/1059)² / (17·99/1059) ≈ 1.59`, and `303.53 + 1.59 ≈ 305.12`. Same cohort (1059) and
same categories as the passing trials — **the only difference is the grouped-rows vs pivoted-columns
chi-square shape.** This failure is **not attributable to any semantics content**; it is an
independent SQL-formula slip that could equally have hit the baseline arm.

### M2 trial2 (FAIL): cohort restricted to *sequenced* patients — plausibly semantics-nudged
Final chi SQL adds a population restriction absent from every passing trial:
```sql
mut_participants AS (SELECT DISTINCT ParticipantBarcode FROM Mutation_Data WHERE FILTER='PASS'),
sequenced AS (SELECT … FROM brca_female bf
              JOIN mut_participants mp ON bf.barcode = mp.ParticipantBarcode  -- ⬅ restricts cohort
              LEFT JOIN cdh1_participants cp ON …)
```
The `JOIN mut_participants` drops every BRCA-female patient who has **no** passing mutation of any
gene from the `Not Mutated` cell, shrinking marginal totals → **`325.3373313351742`**. The GT
denominator is *all* patients, not just sequenced ones.

**Was semantics the cause?** The M2 Scent (all `molecular_database`) injected a Best Practice —
*"WHEN a question asks for high-confidence or 'reliable' mutations, use `FILTER='PASS'` as the
canonical **quality subset**"* — and Gotchas emphasising *"decide the grain first (participant
vs sample vs aliquot)"*. Combined with the question's phrase *"only reliable mutation entries,"*
this plausibly nudged the agent to treat "reliable mutations" as a **cohort filter** (restrict to
sequenced participants) rather than a per-mutation flag. That is a defensible causal link — but
it is one over-interpretation of conditional prose, not a wrong fact.

### Verdict on B: the "regression" is half real, half noise
Of the two M2 failures, **only trial2 is plausibly semantics-attributable**; trial1 is an
independent chi-square-shape bug. n=3. The honest read: **M2 regressed Q3 by roughly one trial's
worth of semantics-nudged over-restriction, riding on top of ordinary chi-square implementation
variance.** Crucially, **none of the authored semantics addressed Q3's actual difficulty**
(cohort = all-vs-sequenced, zero-cell handling, marginal exclusion) — because the author only saw
the irrelevant database. Semantics here **added tail risk without buying any grounding**.

---

## Failure taxonomy (root cause of each, by layer)

| # | Failure | Scent CONTENT | RETRIEVAL | AUTHORING PROMPT | VERIFICATION | AGENT | Pinned root cause |
|---|---------|:---:|:---:|:---:|:---:|:---:|---|
| A | pancancer:1 (both arms) | — (silent) | — (silent) | — | — | ✓ picks name col | **DB-coverage gap**: `clinical_info` never profiled → ROLE/Dimensions never emitted → agent GROUPs by the name column and emits names where the validator wants ICD-O codes |
| B-t1 | pancancer:3 M2 trial1 | — | — | — | — | ✓ drops zero cell | **Chi-square shape**: grouped-rows omits the empty `(Mucinous, Mutated)` cell (−1.59) → 303.53. Independent of semantics |
| B-t2 | pancancer:3 M2 trial2 | ✓ "reliable→PASS quality subset" + grain Gotcha | ranking displaced Dimensions | ✓ conditional prose over-trusted | (claim itself is a true fact) | ✓ over-restricts cohort | **Semantics over-application**: "reliable mutations" read as a cohort filter → sequenced-only denominator → 325.34 |

**One structural cause dominates:** the Cartographer/semantics stack is **blind to attached
Postgres/SQLite databases**, which is where the hard grounding lives on pancancer (and on any
multi-DBMS DAB task). Failure A is a direct consequence; Failure B's semantics harm is a
*symptom of the same blindness* — the author spent its whole budget on the one DB it could see,
which happened to be the one the query barely needed.

---

## Improvement plan (each tied to evidence, each falsifiable by re-ablation)

### C1 — Profile attached Postgres/SQLite DBs in the Cartographer *(highest leverage; fixes A's precondition)*
**Change:** in `env.py::build_dab_task_env`, construct a real `PostgresConnection` /
`SqliteConnection` for each `attachable` and add it to `ctx.connections` (the adapters exist —
CLAUDE.md lists 7). Then `_run_cartographer`/`generate_scent` will profile `clinical_info`
automatically (they already loop over all connections). Guard with a read-only/short-timeout
profile and skip on connect failure so a downed Postgres can't abort a trial.
**Evidence:** all 12 traces show Scent = `molecular_database` only; both failures hinge on
`clinical_info`. **Falsify:** re-run pancancer:1/:3 with clinical profiled; the Q1 Scent should
now carry `clinical_info` Dimensions/Key Tables. Necessary (not sufficient) precondition for C2.

### C2 — Deterministic code/name-pair detector, emitted on the baseline path *(fixes A on the no-LLM arm)*
**Change:** add a deterministic pass (reuse `_looks_like_code` / `_CODE_SHAPE_RE` from
`semantic_claims.py`) that, per table, flags any **code-shaped** string column that co-occurs with
a sibling **name-shaped** column of similar cardinality/concept, and emit a **verified** note in
`build_key_tables` or a new `build_code_columns` section:
*"`clinical_info.icd_o_3_histology` holds coded values (e.g. `9382/3`); `histological_type` holds
display names — when the question asks for histology **types/codes**, group/filter by the code
column."* This does **not** need the LLM — it is the same deterministic check `verify_role_claim`
already performs, just run at author-time instead of verify-time.
**Evidence:** `icd_o_3_histology` was visible to the agent (turn 7) but never chosen; the ROLE
check *would* pass on these two columns. Even the existing `build_dimensions` "format e.g." path
would surface `icd_o_3_histology format e.g.: 9382/3` (the `/` is in `_UNUSUAL_CHARS`) once clinical
is profiled — but that is a weak breadcrumb; an explicit code/name note is the real fix.
**Falsify:** with C1+C2, re-run pancancer:1; expect the agent to GROUP BY `icd_o_3_histology` and
pass ≥2/3. If it still picks the name column despite an explicit note, the gap is AGENT, not Scent.

### C3 — Tighten `_SEMANTICS_INSTRUCTION` against cohort-filter over-application *(addresses B-t2)*
**Change:** add an explicit anti-pattern to the Best Practices grammar:
*"A data-**quality** filter (e.g. `FILTER='PASS'`, 'reliable', 'high-confidence') scopes **which
mutation rows count as a positive**, NOT which patients enter the cohort — never use it to
restrict the population/denominator unless the question explicitly asks to limit to sequenced
patients."* Also require every Best Practice bullet to name whether it affects **row inclusion**
or the **positive-class definition**.
**Evidence:** trial2's `JOIN mut_participants` restriction traces to the "reliable→PASS canonical
quality subset" Best Practice + grain Gotcha. **Falsify:** re-run pancancer:3 M2 with the revised
instruction; trial2-style sequenced-restriction should disappear (target 3/3, matching baseline).

### C4 — Scope semantics authoring to query-relevant tables / suppress when the profiled DB is off-topic *(reduces B's dead-weight risk)*
**Change:** the M2 harm on Q3 was *spending the whole semantics budget on `molecular_database`
gotchas that Q3 barely used*, while retrieval-ranking those high-`matched_terms` prose blocks
**above** the deterministic Dimensions. Two low-risk options: (a) down-weight LLM `draft`-source
sections vs `verified` sections in `search_reference_docs` ranking so authored prose can't displace
verified structure; (b) only author semantics for tables that pass a relevance/join-centrality
bar. **Evidence:** M2 Q3 retrieval surfaced Gotchas/Best-Practices (score 2–3) and pushed
`Dimensions` out of the top sections; the prose addressed the wrong DB. **Falsify:** re-ablate M2
with `verified`-preferred ranking; Q3 should recover toward 3/3 with no loss on tasks where
semantics helps.

### C5 — Chi-square/contingency robustness is an AGENT-prompt issue, not a Scent issue *(addresses B-t1; do not over-invest)*
**Change:** trial1's dropped-zero-cell bug is not a grounding gap and **no Scent change fixes it**.
If worth addressing, it belongs in the agent/system prompt or a `workflow` SOP note: *"build the
full category×outcome grid (CROSS JOIN + `coalesce(count,0)`) before summing chi-square; never
`GROUP BY (row,col)` on observed data, which silently drops empty cells."* **Falsify:** add the
note; trial1-style 303.53 undercounts should vanish. Flagged mainly so it isn't misattributed to
semantics in the ablation.

---

## Verdict

**Valuable-but-mis-implemented — with the emphasis on a specific, fixable structural gap, not a
conceptual flaw.** The Cartographer's ROLE / code-vs-name mechanism is *precisely* the right tool
for the dominant failure (A) and would fire correctly on `icd_o_3_histology` vs `histological_type`
— it is simply never invoked on the table that matters, because attached Postgres/SQLite DBs are
excluded from the profiled connection set. Fix that (C1), make code/name detection deterministic so
it lands on the no-LLM path (C2), and Failure A becomes addressable with a clean falsification test.

The semantics layer's net-harm on Q3 is **real but small and mostly a symptom of the same
blindness**: it authored guidance for the only database it could see (the wrong one), added a
"reliable→quality-subset" rule the agent over-applied to the cohort (B-t2), and displaced verified
structure in retrieval — while the actual failure levers (cohort definition, zero-cells) went
untouched. That is fixable (C3, C4), not fundamental. The remaining M2 loss (B-t1) is ordinary
chi-square implementation variance and should not be counted against semantics at all. **Net: the
n=3 "semantics regressed pancancer" signal is ~half a real, addressable over-application and ~half
noise — do not read it as "semantics is fundamentally harmful," but also do not enable semantics on
a submission until C3/C4 land and the DB-coverage gap (C1) lets it author for the right tables.**

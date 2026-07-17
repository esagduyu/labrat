# LabRat Competitive Refresh — DataAgentBench Field (2026-07-16)

> Delta-refresh of [`competitive-analysis-2026-07-03.md`](competitive-analysis-2026-07-03.md), triggered by (a) our GPT-5.6 submission ([PR #72](https://github.com/ucbepic/DataAgentBench/pull/72), 74.18% stratified, opened today) and (b) two new pending competitor submissions filed 2026-07-15. Sources: DAB repo `origin/main` README at `7a20307` (fetched 2026-07-16), PR queue via `gh`, [ucbepic.github.io/DataAgentBench](https://ucbepic.github.io/DataAgentBench/), [alkera.ai](https://www.alkera.ai/), [docs.alkera.ai](https://docs.alkera.ai/), [Alkera AI LinkedIn (YC S26)](https://www.linkedin.com/company/alkera-ai), [AlkeraAI/Alkera-DAB-July](https://github.com/AlkeraAI/Alkera-DAB-July).

---

## 0. TL;DR

- **Our PR #72 (74.18%) lands #3 on today's board — 0.15pp behind Spacedock (74.33%).** A single task flipping either way in the maintainer regrade swings rank 2↔3. Above us only: SCRIBE (81.85%, maintainer-footnoted as DAB-rule-prompted) and Spacedock.
- **But the board is about to move again: Alkera filed two pending PRs on 07-15** — Opus 4.8 at 80.44% ([#69](https://github.com/ucbepic/DataAgentBench/pull/69)) and **Fable 5 + Opus-4.8-fallback at 83.28%** ([#70](https://github.com/ucbepic/DataAgentBench/pull/70), would be new #1). If both accept before/with ours, we debut **#5**.
- **Alkera is the new most-dangerous competitor** — a YC S26 *product* company (data-engineering agent, IDE/CLI, column-level lineage, "living knowledge base built from your team's work", safety/cost governance) whose wedge overlaps ours far more than Altimate's: lineage + compounding team memory + terminal-native. And they're first on the board with a Mythos-class model.
- **The "grounding layer, not a bigger model" story needs a v2.** We're now also on a current-gen model (GPT-5.6, albeit the *luna* mid-tier). The defensible evolution: **"provider-aware grounding"** — we are the only team publishing per-backbone ablations (Cartographer +8pp on Sonnet / regresses on GPT-5.x, twice-confirmed; hints+ledger carried GPT's lift) and shipping provider-conditional defaults. Everyone else ships one stack and one number.
- **PR #72 rejection risk is low.** Precedent: maintainers footnote disclosed prompt/technique caveats (SCRIBE note ⁶, DataBridge note ²) rather than reject; our AG News caps are score-*conservative* (0/10 failures) and pre-disclosed with an offer to re-represent.

## 1. Board snapshot (README @ `7a20307`, 2026-07-16) + pending queue

| Rank | Entry | Pass@1 | Date | Note |
|---|---|---|---|---|
| 1 | SCRIBE (Actioneer) — Opus 4.7 | 0.8185 | 06-26 | footnote ⁶: DAB-specific domain-rule prompt |
| 2 | Spacedock (Recce) — GPT-5.5 | 0.7433 | 06-23 | |
| 3 | Altimate Code — GPT-5.5 + Sonnet 4.6 | 0.7171 | 06-01 | |
| 4 | **AgenDA — Opus 4.8** | 0.6911 | **07-10** | **new**: HTN planner + tree-pruning recovery + offline DB profiler ([#68](https://github.com/ucbepic/DataAgentBench/pull/68), author `dinogent`, research-style) |
| 5 | Altimate Code — Sonnet 4.6 | 0.6822 | 05-10 | |
| 6 | Spacedock — Opus 4.8 | 0.6721 | 06-08 | |
| 7 | MinusX — Sonnet 4.6 +mini/Haiku | 0.6518 | 05-21 | |
| 8 | DataBridge — GLM-5.2 | 0.6137 | 06-22 | footnote ² |
| 9 | Pi Coding Agent — Opus 4.6 | 0.6103 | 04-21 | |
| **10** | **LabRat — Sonnet 4.6 + Cartographer** | **0.6088** | 06-24 | us (stays on board as the Sonnet entry) |
| 11–24 | PromptQL ×2, Spacedock 4.6, baselines, LabRat #15 (0.5138), Oracle Forge, nQuery… | | | unchanged since 07-03 except nQuery renamed (#56, 0.4547, #17) |

**Pending leaderboard PRs (submission order matters for debut optics):** [#69](https://github.com/ucbepic/DataAgentBench/pull/69) Alkera Opus 4.8 · 80.44% (07-15) → would be #2; [#70](https://github.com/ucbepic/DataAgentBench/pull/70) Alkera Fable 5 + Opus 4.8 fallback · **83.28%** (07-15) → would be **#1**; [#72](https://github.com/ucbepic/DataAgentBench/pull/72) **LabRat GPT-5.6 Luna Max · 74.18%** (07-16) → #3 on today's board, **#5 after both Alkera entries**.

## 2. What changed since 07-03

1. **AgenDA entered at #4 (69.11%, Opus 4.8).** Hierarchical-task-network planner + leaf code executor + *tree-pruning* local failure recovery + an offline database profiler (their analogue of our Cartographer). Research-flavored, no product signal found. Displaced everyone below #3 by one; our Sonnet entry slid #9→#10 without score change.
2. **Alkera arrived (pending).** See dossier below — this is the entry to take seriously.
3. **nQuery (NGENUX) re-labeled** on the board (0.4547, #17) — no competitive significance.
4. **Datasets may move off Git LFS to a Hugging Face mirror** ([#62](https://github.com/ucbepic/DataAgentBench/pull/62), open) — operational: our setup docs/scripts assume LFS; watch for merge.
5. **civic_unstructured GT recompute open** ([#64](https://github.com/ucbepic/DataAgentBench/pull/64)) — unofficial dataset, no leaderboard impact.

## 3. New-competitor dossier: Alkera (YC S26) — *watch closely*

- **Product** ([alkera.ai](https://www.alkera.ai/), [docs.alkera.ai](https://docs.alkera.ai/)): "the data engineering agent" — works in IDE or CLI across the data stack. Pillars: **column-level lineage** via native connectors, a **"living knowledge base built from your team's work"** (their words — this is our correction-harvesting/Scent moat, as a headline feature), **action-level safety gating** (writes/drops/exports gated, human input for destructive ops), **query cost pre-estimation with caps**, credential isolation from the agent.
- **DAB technique** ([#70](https://github.com/ucbepic/DataAgentBench/pull/70), [methods repo](https://github.com/AlkeraAI/Alkera-DAB-July)): Fable 5 at max reasoning with Opus 4.8 xhigh fallback *for Fable safety refusals*; native SQL tools with paginated result objects; **data lineage as an agent tool**; **review subagents**; a SCRIBE-inspired but claimed benchmark-agnostic system addendum (answer-delivery contract + literal-reading discipline + analytical conventions). Full traces published; self-audited with three independent checkers.
- **Their tail is our tail:** perfect 1.00 on seven datasets, but DEPS_DEV_V1 0.50, GITHUB_REPOS 0.50, agnews 0.45, PANCANCER 0.67 — the same four we and everyone else fail. The frontier of this benchmark is now entirely in the idiosyncratic-GT tail, which supports our "levers B/C/D + free-text grounding" autopsy read.
- **Overlap with our wedge is near-total**: terminal/IDE-native ✓, lineage ✓ (we shipped `explain_lineage` in M3), compounding team knowledge ✓ (Scent/harvest), safety/verification posture ✓, evals culture (unknown). Differences to press: we're **AGPL open-core with local-first Scent** (they look closed/SaaS), our knowledge layer is **provenance-stamped + human-gated + contamination-audited**, and we publish **per-backbone measurements**.

## 4. Positioning: v2 of the story

**Old claim (still true for the #10 entry):** "the only top-10 entry on a single mid-tier model — grounding layer, not a bigger model."

**New claim set (for PR #72 and product copy):**
1. **Provider-aware grounding, measured per backbone.** "The same grounding stack is not model-neutral: Cartographer lifts Sonnet +8pp and *regresses* GPT-5.x; hints+context-ledger carry GPT. We measure this per backbone (n=45 matched-key ablations, published) and ship provider-conditional defaults. Nobody else on the leaderboard publishes per-backbone grounding ablations."
2. **Honest scores, fully traced, still top-tier.** 74.18% with zero fitted domain rules (contrast: SCRIBE footnote ⁶), a disclosed score-conservative evaluator cap on 2 quota-bound queries (0/10 — it *lowered* our score), byte-identically rebuildable artifacts, and an independent adversarial audit of the whole campaign.
3. **Mid-tier economics.** GPT-5.6 *luna* (subscription mid-tier) at #3-today beats every Opus 4.8 entry on the board except none — AgenDA (Opus 4.8) is 5pp *below* us; Spacedock Opus 4.8 is 7pp below. "Frontier results at mid-tier cost" is quantifiable from our published usage telemetry (71.15% cache ratio, API-equivalent pricing tables in the campaign report).

**Do NOT claim:** that we beat Spacedock (0.15pp gap is noise — say "statistically tied for #2-tier"); anything implying the AG News caps helped the score (they produced 0/10 failures; always state this); "only mid-tier model in the top 5" *after* Alkera lands (Fable 5 luna-class comparisons get murky — verify tiers before using); that Cartographer is universally positive (our own data says it's Sonnet-conditional — this honesty IS the differentiator).

## 5. PR #72 implications

- **Acceptance risk: low.** Precedent shows disclosed caveats get footnotes, not rejections (SCRIBE ⁶, DataBridge ², LabRat ³). Our disclosure set (hints=Yes, AG News caps with 0/10 outcome + offer to re-represent, 16 CRM suspicious-not-proven trials, trace contract) is stronger than any accepted entry's.
- **Likely maintainer asks:** regrade under their environment (they'll reproduce 206/270 — our own harness confirms), possibly a footnote on the AG News caps, possibly a question about the `[trial exhausted…]` terminal artifacts. All pre-answered in the PR body.
- **Timing:** if maintainers process 69/70 first, we debut #5 instead of #3. No action available — do not rush them; the entry's claims don't depend on rank.
- **Keep the Sonnet entry (#10) on the board** — it anchors the provider-conditional story (same grounding layer, two backbones, two published results).

## 6. Watch triggers

1. **Alkera PRs #69/#70 accepted** → refresh README/claims; check their METHODS.md + traces for verification technique worth learning (review subagents, lineage-as-tool).
2. **Alkera ships customer-facing evals or a corrections log** → our moat window narrows; accelerate moat 2.4.
3. **Spacedock razorback ADE-bench specs materialize into a submission** → our 80% ADE score gets a funded challenger (standing watch item from 07-03).
4. **DAB LFS→HF migration (#62) merges** → update `download.sh`/setup docs; verify `dab_setup.py` still works.
5. **Any regrade that moves our 206/270** → the audit report + byte-identical rebuild is the response kit.
6. **SCRIBE ICDM paper publishes** → their technique becomes copyable by everyone; the fitted-prompt footnote becomes the community's problem.

## 7. Deep-dive supplement (added later same day)

Six trace-level submission audits (Alkera #69/#70, SCRIBE #67, Spacedock #63,
Altimate #53, AgenDA #68, MinusX #50) — full synthesis, per-competitor digests,
and the ranked 7-item adoption backlog live in
**[competitive-deepdives-2026-07-16.md](competitive-deepdives-2026-07-16.md)**.
Headline synthesis: the wrong-answer-taxonomy lever pack is triple-proven at the
top of the board (our pre-identified autopsy levers B+D); nobody else burns LLM
tokens on bulk classification; the only working consensus design is MinusX's
debate (AGPL source); our no-shell registry is a proven differentiator (two of
the top three had shell-path leakage episodes); and per-backbone asymmetry is
universal but measured only by us.

## 8. Outcome update (2026-07-16, end of day)

All pending submissions processed, board now 27 entries: **Alkera #1 (0.8328,
Fable-5+Opus) and #3 (0.8044, Opus)** · SCRIBE #2 (0.8185) · Spacedock #4 (0.7433)
· **LabRat #5 (0.7418, accepted as-is — no adjustment, no footnote)**. The
maintainers also added a **"Tuned prompt ✓" column** (DAB-specific up-front prompts
built from close study of the benchmark's task conventions): ranks 1–3 all carry
it; **among untuned entries LabRat is #2, 0.15pp behind Spacedock** — the
positioning wedge from §4 is now institutionalized by the benchmark itself.
Screenshot: `docs/images/dab-leaderboard-2026-07-16.png`.

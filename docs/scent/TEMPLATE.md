---
kind: scent
domain: <short_domain_slug>          # e.g. ecommerce_sales — also the dedup key
tables: [<table_a>, <table_b>]       # optional, reserved (not scored yet)
confidence: verified                 # verified | draft
---

<!--
Write each section "for retrieval by an LLM": short, factual, routing-trigger
phrasing ("IF the question is about X, use Y / DO NOT use Z"), not prose essays.
Drop this file at:  ./labrat_maze/scent/<domain>.md  (project, version-controlled)
              or:   ~/.labrat/maze/<profile>/scent/<domain>.md  (personal)
Project docs win over personal docs on a domain-name conflict.
-->

## Quick Reference
Business context in 2-3 lines. The grain of the core table(s). Standard hygiene
filters every query in this domain should apply.

## Dimensions
How the key business concepts encode across tables (status codes, currency,
date semantics, soft-delete flags).

## Key Tables
For each canonical table: its grain, scope/exclusions, the join keys to reach it,
and when to use it (the usage trigger).

## Gotchas
Wrong-answer modes a senior analyst would warn a newcomer about. One bullet each.
(e.g. "Date is dirty mixed-format text; parse before any date math.")

## Best Practices
Preferred patterns, canonical metric definitions, columns to prefer/avoid.

## Cross-References
Related domains/docs and when to consult them.

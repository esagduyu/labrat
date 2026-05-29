# DAB Local DB Setup

## Connection URIs (development workstation)

- PostgreSQL: `postgresql://<USER>@localhost:5432/<dataset_db>`
- MongoDB: `mongodb://localhost:27017`

(Replace `<USER>` with `$USER` from the install shell; replace `<dataset_db>` with the per-dataset name created by `scripts/dab_setup.py`.)

## Brew installs

- `postgresql@17` started via `brew services`
- `mongodb-community@8.0` started via `brew services`

## Per-dataset notes (loaded 2026-05-29)

### PostgreSQL databases

| DB name | Source dataset | Tables |
|---|---|---|
| bookreview_db | query_bookreview | 1 (`books_info`) |
| crm_support | query_crmarenapro | 6 (`Case`, `casehistory__c`, `emailmessage`, `issue__c`, `knowledge__kav`, `livechattranscript`) |
| cve_kev | query_cve | 2 (`kev_entries`, `kev_vendor_aliases`) |
| googlelocal_db | query_googlelocal | 1 (`business_description`) |
| movies_db | query_imdb | 15 (`aka_title`, `comp_cast_type`, `company_name`, `company_type`, `complete_cast`, `info_type`, `keyword`, `kind_type`, `link_type`, `movie_companies`, `movie_info`, `movie_info_idx`, `movie_keyword`, `movie_link`, `title`) |
| pancancer_clinical | query_PANCANCER_ATLAS | 1 (`clinical_info`) |
| patent_cpcdefinition | query_PATENTS | 1 (`cpc_definition`) |
| usaspending_contracts | query_usaspending | 2 (`contract_amounts`, `contracts`) |

All 8 PG databases loaded cleanly. Non-fatal `ERROR: role "postgres" does not exist` warnings appear during load (ownership `GRANT` statements in the dump); data loads successfully regardless.

### MongoDB databases

| DB name | Source dataset | Collections |
|---|---|---|
| articles_db | query_agnews | `articles` (127,600 docs) |
| civic_db | query_civic_unstructured | `civic_docs` (19 docs) |
| cve_descriptions | query_cve | `cve_documents` (71,501 docs) |
| domain_docs_db | query_krama | `files` (1,651 docs) |
| usaspending_descriptions | query_usaspending | `contract_documents` (9,921 docs) |
| yelp_db | query_yelp | `business` (100 docs), `checkin` (90 docs) |

**Note:** `mongorestore` must be called *without* `--db` because the DAB dump layout is `dump_folder/<db_name>/<collection>.bson` — the nested directory is the database name. This fix was applied to `scripts/dab_setup.py` (`mongo_load_dataset`).

### Skipped (no PG/Mongo clients — file-based only)

- `query_DEPS_DEV_V1` — SQLite + DuckDB only
- `query_GITHUB_REPOS` — SQLite + DuckDB only
- `query_music_brainz_20k` — SQLite + DuckDB only
- `query_PATENTS` SQLite client (`patent_publication.db`) — 5 GB DuckDB file requires separate `gdown` step; **not downloaded** (see `~/repos/DataAgentBench/download.sh`)
- `query_stockindex` — SQLite + DuckDB only
- `query_stockmarket` — SQLite + DuckDB only

## Verification (2026-05-28)

- PG version: `PostgreSQL 17.10 (Homebrew) on aarch64-apple-darwin25.4.0, compiled by Apple clang version 21.0.0 (clang-2100.0.123.102), 64-bit`
- Mongo default databases: `[ 'admin', 'config', 'local' ]`
- `$USER` = `ege` (use `postgresql://ege@localhost:5432/<dataset_db>` for local connections)

# DAB Local DB Setup

## Connection URIs (development workstation)

- PostgreSQL: `postgresql://<USER>@localhost:5432/<dataset_db>`
- MongoDB: `mongodb://localhost:27017`

(Replace `<USER>` with `$USER` from the install shell; replace `<dataset_db>` with the per-dataset name created by `scripts/dab_setup.py`.)

## Brew installs

- `postgresql@17` started via `brew services`
- `mongodb-community@8.0` started via `brew services`

## Per-dataset notes

(Populated by `scripts/dab_setup.py` runs — see that script's output.)

## Verification (2026-05-28)

- PG version: `PostgreSQL 17.10 (Homebrew) on aarch64-apple-darwin25.4.0, compiled by Apple clang version 21.0.0 (clang-2100.0.123.102), 64-bit`
- Mongo default databases: `[ 'admin', 'config', 'local' ]`
- `$USER` = `ege` (use `postgresql://ege@localhost:5432/<dataset_db>` for local connections)

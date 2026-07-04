# PJM Pipeline

Local PostgreSQL pipeline for PJM DataMiner2 data — LMPs, metered load, generation mix, and
generation capacity. Built to support LCOC (Levelized Cost of Compute) datacenter-electricity
analysis: locational energy price, demand growth, and supply mix for the zones where hyperscale
datacenters concentrate.

## Prerequisites

- Python 3.12+
- PostgreSQL 14+
- PJM DataMiner2 API key ([register here](https://dataminer2.pjm.com))

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in DB credentials and PJM_API_KEY
python db_setup.py            # creates schema (idempotent, safe to re-run)
```

## Schema

| Table | Grain | Populated by | Purpose |
|---|---|---|---|
| `pjm_pnodes` | one row per pricing node | `ingest_pnodes.py` | Lookup/master — zone and hub node identifiers |
| `pjm_da_lmp` | hourly, per node | `ingest_lmp.py --feed da` | Day-ahead LMP: energy + congestion + marginal loss |
| `pjm_rt_lmp` | hourly, per node | `ingest_lmp.py --feed rt` | Real-time LMP — DA/RT spread is a volatility signal |
| `pjm_load_metered` | hourly, per zone | `ingest_load.py` | Metered load by zone — demand growth proxy |
| `pjm_gen_by_fuel` | hourly, per fuel type | `ingest_gen.py` | Generation mix: coal, gas, nuclear, solar, wind, etc. |
| `pjm_gen_capacity` | hourly, RTO-wide | `ingest_gen_capacity.py` | Economic/emergency max MW + RPM committed capacity (system-wide, not zonal) |
| `pjm_ingest_log` | one row per (feed, date) | all ingest scripts | Tracks loaded dates so `--incremental` skips already-loaded days |

Indexes on `(time)` and `(zone/area, time)` for time-series and zone-filter queries.

## Files

```
config.py             — DB credentials, API key, zone/hub scope, feed name map
pjm_client.py          — DataMiner2 HTTP client: pagination, rate limiting, retry/backoff
db_setup.py            — creates all tables and indexes
ingest_pnodes.py       — one-time/quarterly pnode master load
ingest_lmp.py          — DA and/or RT LMP ingest
ingest_load.py         — hourly metered load ingest
ingest_gen.py          — hourly generation-by-fuel ingest
ingest_gen_capacity.py — hourly RTO-wide generation capacity ingest
test_connection.py     — smoke test: verifies API key and connectivity
weekly_update.sh       — runs all four incremental ingests in sequence; cron entry point
logs/                  — weekly_update.sh output, one line per cron run
01_lmp_explorer.ipynb  — exploratory notebook: zone comparison, DA/RT spread,
                         congestion ranking, fuel mix, DOM load vs LMP
```

## Running

```bash
python ingest_pnodes.py                                         # run once (or quarterly)
python ingest_lmp.py --feed both --start 2022-01-01 --end 2026-06-28
python ingest_load.py --start 2022-01-01 --end 2026-06-28
python ingest_gen.py --start 2022-01-01 --end 2026-06-28
python ingest_gen_capacity.py --start 2022-01-01 --end 2026-06-28
```

All four backfill scripts support `--incremental`, which resumes from the last loaded date
rather than requiring explicit `--start`/`--end`:

```bash
python ingest_lmp.py --feed both --incremental
```

## Scheduling

`weekly_update.sh` runs all four incremental ingests (LMP, load, gen, gen capacity) in
sequence and logs output. Scheduled via cron to run every Monday morning:

```bash
0 6 * * 1 /home/daniel/projects/pjm_pipeline/weekly_update.sh >> /home/daniel/projects/pjm_pipeline/logs/weekly_update.log 2>&1
```

It continues past a failed step so one bad feed doesn't block the others, but exits
non-zero overall if any step failed — check `logs/weekly_update.log` after each run.

## Rate limiting

The non-member DataMiner2 tier allows 6 requests/minute. The client enforces an 11-second
inter-request delay, so a multi-year backfill takes several hours per feed — plan to run it
overnight. Use only one ingest script at a time against a given API key; running them in
parallel will exceed the shared rate limit.

## Scope

Default zones in `config.py`: `DOM, AEP, COMED, PECO`. Default hubs: `AEP-DAYTON HUB,
WESTERN HUB, EASTERN HUB, NEW JERSEY HUB`. The LMP ingest fetches all nodes of the requested
type (ZONE or HUB) — the lists in `config.py` are reference documentation, not API filters.
Backfill defaults to `2022-01-01`.

## Capacity market note

`pjm_gen_capacity` (the `day_gen_capacity` DataMiner2 feed) is PJM-RTO system-wide only —
no zone or LDA breakdown exists in this feed. Zone-level RPM capacity clearing prices (e.g.
DOM LDA) are published separately by PJM as auction result PDFs/spreadsheets and are not
available via DataMiner2.

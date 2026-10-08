# PJM Pipeline

Local PostgreSQL pipeline for PJM DataMiner2 data — LMPs, metered load, generation mix, and
generation capacity. Built to support LCOC (Levelized Cost of Compute) datacenter-electricity
analysis: locational energy price, demand growth, and supply mix for the zones where hyperscale
datacenters concentrate.

## Background

I converted my c. 2017 Dell OptiPlex to Linux with a new 1TB SSD and 16GB of RAM, and do my
development work using VS Code over SSH from my Windows 11 desktop. PostgreSQL was already
installed on the Linux box.

The initial ingest of DataMiner2 data took about 36 hours, over a weekend, running under a
non-member API rate limit. A cron job now runs on the Linux box early Monday mornings to
incrementally ingest new data each week. PJM's most recent data can be incomplete or revised,
so the cron job is designed to back-fill accordingly.

## Prerequisites

- Python 3.12+
- PostgreSQL 14+
- PJM DataMiner2 API key ([register here](https://dataminer2.pjm.com))

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in DB credentials and PJM_API_KEY
python db_setup.py            # creates schema (idempotent, safe to re-run)
python install_queries.py     # installs saved query functions (idempotent, safe to re-run)
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
| `weather_locations` | one row per weather location | `ingest_weather.py` (synced from `config.WEATHER_LOCATIONS`) | Location → zone mapping and weights for zone-level weather |
| `weather_zone_hourly` (view) | hourly, per zone | view over `weather_hourly` | Weighted zone weather — **use this for zone-level analysis**, not `weather_hourly` directly |
| `weather_hourly` | hourly, per weather location | `ingest_weather.py` | Observed (ERA5) + GFS ~24h/~48h-ahead forecasts of temperature, wind, humidity, cloud cover, plus PJM's WWP/THI indices — exogenous features for DA/RT price models |
| `pjm_ingest_log` | one row per (feed, date) | all ingest scripts | Tracks loaded dates so `--incremental` skips already-loaded days |
| `pjm_nerc_holidays` | one row per NERC holiday date | seeded by `db_setup.py` | On-peak/off-peak classification for `pjm_lmp_monthly_peak()` — seeded 2022-2032 |

Indexes on `(time)` and `(zone/area, time)` for time-series and zone-filter queries.

**Timestamps.** Every `datetime_*_utc` column is a true UTC `TIMESTAMPTZ`. DataMiner2 returns
its `*_utc` fields as naive strings with no offset. Passed to Postgres as-is, they'd be read
in the session timezone (`America/Chicago` on this box), so every ingest script parses them
through `pjm_client.parse_utc()`, which attaches UTC explicitly. Any new ingest script must
do the same. Before 2026-10-08 that step was missing: all PJM rows sat 5–6 hours late, and
one hour per year was lost at each spring-forward. The data was repaired in place on that date.

Spring-forward days have 23 hours and fall-back days 25, as expected. For time-of-day or
calendar features, convert with `AT TIME ZONE 'America/New_York'` (EPT), and keep UTC as the
row key.

## Files

```
config.py             — DB credentials, API key, zone/hub scope, feed name map
pjm_client.py          — DataMiner2 HTTP client: pagination, rate limiting, retry/backoff
db_setup.py            — creates all tables and indexes
queries.sql            — saved SQL functions for ODBC/BI access (see Saved queries below)
install_queries.py     — installs/updates the functions in queries.sql
ingest_pnodes.py       — one-time/quarterly pnode master load
ingest_lmp.py          — DA and/or RT LMP ingest
ingest_load.py         — hourly metered load ingest
ingest_gen.py          — hourly generation-by-fuel ingest
ingest_gen_capacity.py — hourly RTO-wide generation capacity ingest
ingest_weather.py      — hourly observed + forecast temperature per zone (Open-Meteo)
test_connection.py     — smoke test: verifies API key and connectivity
weekly_update.sh       — runs all incremental ingests in sequence; cron entry point
logs/                  — weekly_update.sh output, one line per cron run
01_lmp_explorer.ipynb  — exploratory notebook: zone comparison, DA/RT spread,
                         congestion ranking, fuel mix, DOM load vs LMP
02_lmp_arima_ets.ipynb — ARIMA vs ETS forecasting demo on daily DA LMP, DOM vs AEP
03_lmp_data_validation.ipynb — dense day×hour completeness grid (DA/RT, DOM/AEP) + gap report
04_weather_explorer.ipynb — weather coverage, forecast skill (d1 vs d2), temp vs load/LMP,
                         out-of-sample test of temperature as a price-model feature
```

## Running

```bash
python ingest_pnodes.py                                         # run once (or quarterly)
python ingest_lmp.py --feed both --start 2022-01-01 --end 2026-06-28
python ingest_load.py --start 2022-01-01 --end 2026-06-28
python ingest_gen.py --start 2022-01-01 --end 2026-06-28
python ingest_gen_capacity.py --start 2022-01-01 --end 2026-06-28
python ingest_weather.py --start 2022-01-01                    # ~5 min, Open-Meteo, no PJM key
python ingest_weather.py --start 2022-01-01 --locations RIC,ORF   # backfill specific stations only
```

All four backfill scripts support `--incremental`, which resumes from the last loaded date
rather than requiring explicit `--start`/`--end`:

```bash
python ingest_lmp.py --feed both --incremental
```

## Scheduling

`weekly_update.sh` runs all five incremental ingests (LMP, load, gen, gen capacity, weather) in
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

## Saved queries

`queries.sql` holds SQL functions installed in the database so BI/ODBC tools can call them
directly instead of writing raw joins each time. Install/update with `python install_queries.py`.

**`pjm_lmp_by_node(pnode_name, start_date DEFAULT NULL, end_date DEFAULT NULL)`** — combined
DA + RT hourly LMP series for one node, optionally bounded by date (`end_date` inclusive):

```sql
SELECT * FROM pjm_lmp_by_node('AEP');                              -- full series
SELECT * FROM pjm_lmp_by_node('AEP', '2026-06-30', '2026-07-07');  -- one week
```

Returns `start_time` (= `datetime_beginning_utc`), `da_lmp`, `rt_lmp`. Uses a FULL OUTER JOIN
on `(datetime_beginning_utc)` within the node, so hours where only one of DA/RT loaded show up
with a NULL on the missing side rather than being silently dropped.

Note: `pnode_name` is never ambiguous between ZONE and HUB types (verified — no name in
`pjm_pnodes` has more than one distinct `pnode_type`), so no type filter is needed.

**`pjm_lmp_monthly_peak(pnode_name, start_date DEFAULT NULL, end_date DEFAULT NULL)`** —
average DA + RT LMP by calendar month, split into `ON-PEAK` / `OFF-PEAK`:

```sql
SELECT * FROM pjm_lmp_monthly_peak('AEP');
SELECT * FROM pjm_lmp_monthly_peak('AEP', '2025-01-01', '2025-12-31');
```

Returns `month_start`, `peak_type`, `avg_da_lmp`, `avg_rt_lmp`, `hour_count`. On-peak follows
the standard PJM/Eastern definition — HE 0800-2300 (hour-beginning 07:00-22:00), Monday-Friday,
excluding NERC holidays (`pjm_nerc_holidays`) — evaluated in `America/New_York` local time, not
UTC. Everything else (nights, weekends, holidays) is off-peak. Calendar months are also bucketed
in local time, so `start_date`/`end_date` (UTC-bound, same as `pjm_lmp_by_node`) can pull in or
exclude a handful of hours at the very edge of a month — pass wide bounds if you need clean
month totals, or leave them `NULL` for the full series.

## Capacity market note

`pjm_gen_capacity` (the `day_gen_capacity` DataMiner2 feed) is PJM-RTO system-wide only —
no zone or LDA breakdown exists in this feed. Zone-level RPM capacity clearing prices (e.g.
DOM LDA) are published separately by PJM as auction result PDFs/spreadsheets and are not
available via DataMiner2.

## Weather data

`ingest_weather.py` pulls hourly weather from [Open-Meteo](https://open-meteo.com) for each
point in `config.WEATHER_LOCATIONS`. It needs no API key and is separate from the PJM rate
limit, so it doesn't compete with the PJM ingests. The licence is free for non-commercial
use only.

**Zone weighting.** `weather_hourly` stores one row per location. Zone-level weather comes from
the `weather_zone_hourly` view, a weighted average of the zone's locations:

| Zone | Locations (weight) |
|---|---|
| DOM | Dulles IAD (60%), Richmond RIC (20%), Norfolk ORF (20%) |
| AEP | Columbus CMH (100%) |
| COMED | Chicago O'Hare ORD (100%) |
| PECO | Philadelphia PHL (100%) |

Weights live in `config.py`. Each ingest run syncs them into the `weather_locations` table,
which the view reads. To change a weight, edit config and run the ingest; to add a station,
add it to config and run `--incremental`, which backfills any location that has no data yet.
The view leaves a zone's value `NULL` for any hour where one of its locations is missing
data, so the station mix never silently changes. WWP and THI in the view are recomputed
from the weighted inputs, not averaged from each station's index.

**Don't join `weather_hourly` to price or load tables on `zone`.** DOM has three locations,
so that join triples every DOM row. Join `weather_zone_hourly` instead.

A full backfill since 2022 takes about 60 requests and 5 minutes.

Each variable comes in three versions, named by suffix:

| Suffix | Source | Use |
|---|---|---|
| `_obs_` | Historical Weather API (ERA5 reanalysis) | What actually happened — RT models, load analysis |
| `_fcst_d1_` | Previous Runs API, GFS, issued ~24h before the hour | Day-ahead forecast |
| `_fcst_d2_` | Previous Runs API, GFS, issued ~48h before the hour | Strictly leakage-free DA feature |

| Variable | Columns | Notes |
|---|---|---|
| Temperature | `temp_*_f` | Dry bulb, °F |
| Wind speed | `wind_*_mph` | 10 m, mph |
| Relative humidity | `rh_*_pct` | % |
| Cloud cover | `cloud_*_pct` | % (proxy for solar output and lighting load) |
| Winter Weather Parameter | `wwp_*_f` | Generated column. PJM Manual 19 §3.2: `DB − 0.5·(WIND − 10)` when wind > 10 mph, else `DB` |
| Temperature-Humidity Index | `thi_*_f` | Generated column. PJM Manual 19 §3.2: `DB − 0.55·(1 − HUM)·(DB − 58)` when DB ≥ 58°F, else `DB` |

WWP and THI are Postgres `GENERATED ... STORED` columns, so they stay consistent with their
inputs automatically. They're `NULL` whenever an input is `NULL`, rather than falling back
to plain temperature, so each column is either fully adjusted or missing.

**Watch for leakage in DA models.** DA bids close at about 10:30 EPT on D-1. Don't use
`temp_obs_f` to predict DA prices, because the market couldn't see it yet. For late-day
hours, the `d1` forecast run also comes after the DA close. `d2` is issued before the
close for every hour of the operating day.

Rows are upserted, not inserted once. Open-Meteo fills the most recent ~5 days of the
archive with provisional values that get revised later. `--incremental` therefore
re-fetches the last 14 days on each run.

**Forecast coverage:** observed columns are complete from 2022. Forecast coverage is shorter:

- Temperature forecasts cover 2022 onward, except a gap from 2023-12-29 to 2024-01-20
  (about 500 hours per location).
- Wind, humidity and cloud cover forecasts, and the WWP/THI forecasts built from them, only
  start on **2024-01-20**. That's when Open-Meteo began archiving those GFS variables.

So a DA model using forecast WWP/THI has about 2.7 years of history to train on, not 4.7.

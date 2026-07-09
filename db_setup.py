"""
db_setup.py — Create PJM Pipeline schema in PostgreSQL.
Run once:  python db_setup.py

Safe to re-run: all statements use IF NOT EXISTS or CREATE INDEX CONCURRENTLY.
"""

import logging
import psycopg2
from config import DB

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger(__name__)

SCHEMA_SQL = """

-- ── Pricing node master ────────────────────────────────────────────────────
-- Pull once with ingest_pnodes.py, refresh quarterly.
CREATE TABLE IF NOT EXISTS pjm_pnodes (
    pnode_id            BIGINT,
    pnode_name          TEXT        NOT NULL,
    pnode_type          TEXT,       -- BUS, ZONE, HUB, AGGREGATE, INTERFACE
    voltage_level       TEXT,
    zone                TEXT,
    sub_zone            TEXT,
    effective_date      DATE,
    termination_date    DATE,
    PRIMARY KEY (pnode_id, pnode_name)
);

-- ── Day-ahead hourly LMPs ─────────────────────────────────────────────────
-- Core price series. Start with ZONE/HUB type only (~20 zones).
-- Full nodal is ~12k nodes — add later if needed.
CREATE TABLE IF NOT EXISTS pjm_da_lmp (
    datetime_beginning_utc  TIMESTAMPTZ     NOT NULL,
    datetime_ending_utc     TIMESTAMPTZ     NOT NULL,
    pnode_id                BIGINT,
    pnode_name              TEXT            NOT NULL,
    voltage                 TEXT,
    type                    TEXT,           -- ZONE, HUB, AGGREGATE, INTERFACE, BUS
    lmp                     NUMERIC(10,4),
    congestion_price        NUMERIC(10,4),
    marginal_loss_price     NUMERIC(10,4),
    PRIMARY KEY (datetime_beginning_utc, pnode_name)
);

-- ── Real-time hourly LMPs ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pjm_rt_lmp (
    datetime_beginning_utc  TIMESTAMPTZ     NOT NULL,
    datetime_ending_utc     TIMESTAMPTZ     NOT NULL,
    pnode_id                BIGINT,
    pnode_name              TEXT            NOT NULL,
    voltage                 TEXT,
    type                    TEXT,
    lmp                     NUMERIC(10,4),
    congestion_price        NUMERIC(10,4),
    marginal_loss_price     NUMERIC(10,4),
    PRIMARY KEY (datetime_beginning_utc, pnode_name)
);

-- ── Hourly metered load by zone ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pjm_load_metered (
    datetime_beginning_utc  TIMESTAMPTZ     NOT NULL,
    datetime_ending_utc     TIMESTAMPTZ     NOT NULL,
    area                    TEXT            NOT NULL,
    mw                      NUMERIC(10,2),
    company_verified        BOOLEAN,
    PRIMARY KEY (datetime_beginning_utc, area)
);

-- ── Generation by fuel type (hourly) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS pjm_gen_by_fuel (
    datetime_beginning_utc  TIMESTAMPTZ     NOT NULL,
    datetime_ending_utc     TIMESTAMPTZ     NOT NULL,
    fuel_type               TEXT            NOT NULL,
    mw                      NUMERIC(10,2),
    is_renewable            BOOLEAN         GENERATED ALWAYS AS (
                                fuel_type IN ('Solar','Wind','Hydro',
                                              'Other Renewables')
                            ) STORED,
    PRIMARY KEY (datetime_beginning_utc, fuel_type)
);

-- ── Hourly generation capacity (RPM committed) ────────────────────────────
-- PJM-RTO system-wide only — the day_gen_capacity feed has no zone/area
-- breakdown. For zone/LDA-level capacity prices, see the published RPM BRA
-- auction results (separate source, not this feed).
CREATE TABLE IF NOT EXISTS pjm_gen_capacity (
    datetime_beginning_utc  TIMESTAMPTZ     NOT NULL,
    datetime_ending_utc     TIMESTAMPTZ     NOT NULL,
    economic_max_mw         NUMERIC(10,2),
    emergency_max_mw        NUMERIC(10,2),
    rpm_committed_mw        NUMERIC(10,2),
    PRIMARY KEY (datetime_beginning_utc)
);

-- ── Ingestion audit log ───────────────────────────────────────────────────
-- Tracks what has been loaded so incremental updates skip already-loaded dates.
CREATE TABLE IF NOT EXISTS pjm_ingest_log (
    id              SERIAL          PRIMARY KEY,
    feed            TEXT            NOT NULL,
    date_loaded     DATE            NOT NULL,
    rows_inserted   INTEGER,
    loaded_at       TIMESTAMPTZ     DEFAULT NOW(),
    UNIQUE (feed, date_loaded)
);

-- ── NERC holiday calendar ─────────────────────────────────────────────────
-- The 6 standard NERC holidays, used to classify on-peak vs. off-peak hours
-- (see pjm_lmp_monthly_peak() in queries.sql). Dates are the actual calendar
-- date — NERC holidays are not shifted to an adjacent weekday when they fall
-- on a weekend, since weekends are already off-peak. Seeded through 2032;
-- extend HOLIDAY_SQL in this file and re-run when that runs out.
CREATE TABLE IF NOT EXISTS pjm_nerc_holidays (
    holiday_date    DATE            PRIMARY KEY,
    holiday_name    TEXT            NOT NULL
);

"""

HOLIDAY_SQL = """
INSERT INTO pjm_nerc_holidays (holiday_date, holiday_name) VALUES
    ('2022-01-01', 'New Years Day'),
    ('2022-05-30', 'Memorial Day'),
    ('2022-07-04', 'Independence Day'),
    ('2022-09-05', 'Labor Day'),
    ('2022-11-24', 'Thanksgiving Day'),
    ('2022-12-25', 'Christmas Day'),
    ('2023-01-01', 'New Years Day'),
    ('2023-05-29', 'Memorial Day'),
    ('2023-07-04', 'Independence Day'),
    ('2023-09-04', 'Labor Day'),
    ('2023-11-23', 'Thanksgiving Day'),
    ('2023-12-25', 'Christmas Day'),
    ('2024-01-01', 'New Years Day'),
    ('2024-05-27', 'Memorial Day'),
    ('2024-07-04', 'Independence Day'),
    ('2024-09-02', 'Labor Day'),
    ('2024-11-28', 'Thanksgiving Day'),
    ('2024-12-25', 'Christmas Day'),
    ('2025-01-01', 'New Years Day'),
    ('2025-05-26', 'Memorial Day'),
    ('2025-07-04', 'Independence Day'),
    ('2025-09-01', 'Labor Day'),
    ('2025-11-27', 'Thanksgiving Day'),
    ('2025-12-25', 'Christmas Day'),
    ('2026-01-01', 'New Years Day'),
    ('2026-05-25', 'Memorial Day'),
    ('2026-07-04', 'Independence Day'),
    ('2026-09-07', 'Labor Day'),
    ('2026-11-26', 'Thanksgiving Day'),
    ('2026-12-25', 'Christmas Day'),
    ('2027-01-01', 'New Years Day'),
    ('2027-05-31', 'Memorial Day'),
    ('2027-07-04', 'Independence Day'),
    ('2027-09-06', 'Labor Day'),
    ('2027-11-25', 'Thanksgiving Day'),
    ('2027-12-25', 'Christmas Day'),
    ('2028-01-01', 'New Years Day'),
    ('2028-05-29', 'Memorial Day'),
    ('2028-07-04', 'Independence Day'),
    ('2028-09-04', 'Labor Day'),
    ('2028-11-23', 'Thanksgiving Day'),
    ('2028-12-25', 'Christmas Day'),
    ('2029-01-01', 'New Years Day'),
    ('2029-05-28', 'Memorial Day'),
    ('2029-07-04', 'Independence Day'),
    ('2029-09-03', 'Labor Day'),
    ('2029-11-22', 'Thanksgiving Day'),
    ('2029-12-25', 'Christmas Day'),
    ('2030-01-01', 'New Years Day'),
    ('2030-05-27', 'Memorial Day'),
    ('2030-07-04', 'Independence Day'),
    ('2030-09-02', 'Labor Day'),
    ('2030-11-28', 'Thanksgiving Day'),
    ('2030-12-25', 'Christmas Day'),
    ('2031-01-01', 'New Years Day'),
    ('2031-05-26', 'Memorial Day'),
    ('2031-07-04', 'Independence Day'),
    ('2031-09-01', 'Labor Day'),
    ('2031-11-27', 'Thanksgiving Day'),
    ('2031-12-25', 'Christmas Day'),
    ('2032-01-01', 'New Years Day'),
    ('2032-05-31', 'Memorial Day'),
    ('2032-07-04', 'Independence Day'),
    ('2032-09-06', 'Labor Day'),
    ('2032-11-25', 'Thanksgiving Day'),
    ('2032-12-25', 'Christmas Day')
ON CONFLICT (holiday_date) DO NOTHING;
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_da_lmp_time       ON pjm_da_lmp (datetime_beginning_utc)",
    "CREATE INDEX IF NOT EXISTS idx_da_lmp_zone_time  ON pjm_da_lmp (pnode_name, datetime_beginning_utc)",
    "CREATE INDEX IF NOT EXISTS idx_rt_lmp_time       ON pjm_rt_lmp (datetime_beginning_utc)",
    "CREATE INDEX IF NOT EXISTS idx_rt_lmp_zone_time  ON pjm_rt_lmp (pnode_name, datetime_beginning_utc)",
    "CREATE INDEX IF NOT EXISTS idx_load_time         ON pjm_load_metered (datetime_beginning_utc)",
    "CREATE INDEX IF NOT EXISTS idx_load_area_time    ON pjm_load_metered (area, datetime_beginning_utc)",
    "CREATE INDEX IF NOT EXISTS idx_gen_fuel_time     ON pjm_gen_by_fuel (datetime_beginning_utc)",
]


def setup():
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor()

    log.info("Creating tables...")
    cur.execute(SCHEMA_SQL)
    log.info("Tables created.")

    log.info("Creating indexes...")
    for sql in INDEX_SQL:
        cur.execute(sql)
    log.info("Indexes created.")

    log.info("Seeding NERC holiday calendar...")
    cur.execute(HOLIDAY_SQL)
    log.info("Holiday calendar seeded.")

    cur.close()
    conn.close()
    log.info("Schema setup complete.")


if __name__ == "__main__":
    setup()

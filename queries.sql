-- queries.sql — Saved, parameterized queries for ODBC / BI tool access.
-- Install with: python install_queries.py
-- Safe to re-run: uses CREATE OR REPLACE.

-- pjm_lmp_by_node(pnode_name, start_date, end_date) — combined DA + RT hourly LMP
-- series for one node, optionally bounded by date. start_date/end_date default to
-- NULL (no bound), so the original one-arg call still returns the full series:
--   SELECT * FROM pjm_lmp_by_node('AEP');
-- Pass dates to slice down a large result, e.g. last 7 days:
--   SELECT * FROM pjm_lmp_by_node('AEP', '2026-06-30', '2026-07-07');
-- Drop first: changing the signature (adding params) makes CREATE OR REPLACE
-- register a second overload instead of replacing the old one-arg version.
DROP FUNCTION IF EXISTS pjm_lmp_by_node(TEXT);

CREATE OR REPLACE FUNCTION pjm_lmp_by_node(
    p_pnode_name TEXT,
    p_start_date DATE DEFAULT NULL,
    p_end_date   DATE DEFAULT NULL
)
RETURNS TABLE (
    start_time  TIMESTAMPTZ,
    da_lmp      NUMERIC(10,4),
    rt_lmp      NUMERIC(10,4)
) AS $$
    SELECT
        COALESCE(da.datetime_beginning_utc, rt.datetime_beginning_utc) AS start_time,
        da.lmp AS da_lmp,
        rt.lmp AS rt_lmp
    FROM (
        SELECT datetime_beginning_utc, lmp
        FROM pjm_da_lmp
        WHERE pnode_name = p_pnode_name
          AND (p_start_date IS NULL OR datetime_beginning_utc >= p_start_date)
          AND (p_end_date   IS NULL OR datetime_beginning_utc <  p_end_date + 1)
    ) da
    FULL OUTER JOIN (
        SELECT datetime_beginning_utc, lmp
        FROM pjm_rt_lmp
        WHERE pnode_name = p_pnode_name
          AND (p_start_date IS NULL OR datetime_beginning_utc >= p_start_date)
          AND (p_end_date   IS NULL OR datetime_beginning_utc <  p_end_date + 1)
    ) rt
        ON da.datetime_beginning_utc = rt.datetime_beginning_utc
    ORDER BY start_time;
$$ LANGUAGE sql STABLE;

-- pjm_lmp_monthly_peak(pnode_name, start_date, end_date) — average DA + RT LMP
-- by calendar month, split into ON-PEAK / OFF-PEAK. Peak definition follows
-- standard PJM/Eastern on-peak: HE 0800-2300 (hour-beginning 07:00-22:00),
-- Monday-Friday, excluding NERC holidays (see pjm_nerc_holidays). All other
-- hours — nights, weekends, and holidays — are OFF-PEAK. Classification is
-- done in America/New_York local time, not UTC, since that's the timezone
-- the peak definition is written against.
--   SELECT * FROM pjm_lmp_monthly_peak('AEP');
--   SELECT * FROM pjm_lmp_monthly_peak('AEP', '2025-01-01', '2025-12-31');
DROP FUNCTION IF EXISTS pjm_lmp_monthly_peak(TEXT);

CREATE OR REPLACE FUNCTION pjm_lmp_monthly_peak(
    p_pnode_name TEXT,
    p_start_date DATE DEFAULT NULL,
    p_end_date   DATE DEFAULT NULL
)
RETURNS TABLE (
    month_start  DATE,
    peak_type    TEXT,
    avg_da_lmp   NUMERIC(10,4),
    avg_rt_lmp   NUMERIC(10,4),
    hour_count   BIGINT
) AS $$
    WITH combined AS (
        SELECT
            COALESCE(da.datetime_beginning_utc, rt.datetime_beginning_utc) AS start_time,
            da.lmp AS da_lmp,
            rt.lmp AS rt_lmp
        FROM (
            SELECT datetime_beginning_utc, lmp
            FROM pjm_da_lmp
            WHERE pnode_name = p_pnode_name
              AND (p_start_date IS NULL OR datetime_beginning_utc >= p_start_date)
              AND (p_end_date   IS NULL OR datetime_beginning_utc <  p_end_date + 1)
        ) da
        FULL OUTER JOIN (
            SELECT datetime_beginning_utc, lmp
            FROM pjm_rt_lmp
            WHERE pnode_name = p_pnode_name
              AND (p_start_date IS NULL OR datetime_beginning_utc >= p_start_date)
              AND (p_end_date   IS NULL OR datetime_beginning_utc <  p_end_date + 1)
        ) rt
            ON da.datetime_beginning_utc = rt.datetime_beginning_utc
    ),
    classified AS (
        SELECT
            (start_time AT TIME ZONE 'America/New_York') AS local_time,
            da_lmp,
            rt_lmp
        FROM combined
    )
    SELECT
        date_trunc('month', local_time)::DATE AS month_start,
        CASE
            WHEN EXTRACT(ISODOW FROM local_time) BETWEEN 1 AND 5
             AND EXTRACT(HOUR FROM local_time) BETWEEN 7 AND 22
             AND local_time::DATE NOT IN (SELECT holiday_date FROM pjm_nerc_holidays)
            THEN 'ON-PEAK'
            ELSE 'OFF-PEAK'
        END AS peak_type,
        AVG(da_lmp)::NUMERIC(10,4) AS avg_da_lmp,
        AVG(rt_lmp)::NUMERIC(10,4) AS avg_rt_lmp,
        COUNT(*) AS hour_count
    FROM classified
    GROUP BY month_start, peak_type
    ORDER BY month_start, peak_type DESC;
$$ LANGUAGE sql STABLE;

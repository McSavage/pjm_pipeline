"""
ingest_gen_capacity.py — Ingest PJM hourly generation capacity (RPM committed).

PJM-RTO system-wide only — the day_gen_capacity feed has no zone/area
breakdown, unlike the other ingest scripts. See db_setup.py for details.

    python ingest_gen_capacity.py --start 2022-01-01 --end 2024-12-31
    python ingest_gen_capacity.py --incremental
"""
import argparse
import logging
import time
from datetime import date, datetime, timedelta

import psycopg2

from config import DB, DEFAULT_START_DATE, FEEDS
from pjm_client import PJMClient

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

LOG_FEED_KEY = "gen_capacity"


def get_last_loaded_date(conn, feed_key: str) -> date | None:
    # rows_inserted > 0 so a day that came back empty gets retried on the
    # next run instead of being marked done forever.
    cur = conn.cursor()
    cur.execute(
        "SELECT MAX(date_loaded) FROM pjm_ingest_log WHERE feed = %s AND rows_inserted > 0",
        (feed_key,)
    )
    row = cur.fetchone()
    cur.close()
    return row[0] if row and row[0] else None


def log_loaded_date(conn, feed_key: str, d: date, rows: int):
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO pjm_ingest_log (feed, date_loaded, rows_inserted)
           VALUES (%s, %s, %s)
           ON CONFLICT (feed, date_loaded) DO UPDATE SET
               rows_inserted = EXCLUDED.rows_inserted,
               loaded_at     = NOW()""",
        (feed_key, d, rows)
    )
    conn.commit()
    cur.close()


def insert_capacity_rows(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    cur = conn.cursor()
    sql = """
        INSERT INTO pjm_gen_capacity
            (datetime_beginning_utc, datetime_ending_utc,
             economic_max_mw, emergency_max_mw, rpm_committed_mw)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (datetime_beginning_utc) DO NOTHING
    """
    inserted = 0
    for r in rows:
        # day_gen_capacity only returns bid_datetime_beginning_utc — hourly
        # feed, so the interval ends exactly one hour later.
        beginning = r.get("bid_datetime_beginning_utc")
        ending = (datetime.fromisoformat(beginning) + timedelta(hours=1)).isoformat() if beginning else None

        cur.execute(sql, (
            beginning,
            ending,
            r.get("eco_max"),
            r.get("emerg_max"),
            r.get("total_committed"),
        ))
        inserted += 1
    conn.commit()
    cur.close()
    return inserted


def ingest_gen_capacity(start_date: str, end_date: str):
    client = PJMClient()
    conn   = psycopg2.connect(**DB)

    current = datetime.fromisoformat(start_date).date()
    end     = datetime.fromisoformat(end_date).date()
    total   = 0

    while current <= end:
        day_str = current.strftime("%Y-%m-%d")

        if get_last_loaded_date(conn, f"{LOG_FEED_KEY}_{day_str}"):
            log.info(f"  {day_str} already loaded — skipping")
            current += timedelta(days=1)
            continue

        log.info(f"Fetching gen_capacity {day_str}...")
        params = {"bid_datetime_beginning_ept": day_str}
        rows = client.fetch(FEEDS["gen_capacity"], params)
        time.sleep(client.delay)  # pace requests — daily response fits in one page
        n    = insert_capacity_rows(conn, rows)
        log_loaded_date(conn, f"{LOG_FEED_KEY}_{day_str}", current, n)
        total += n
        log.info(f"  Inserted {n} rows")
        current += timedelta(days=1)

    conn.close()
    log.info(f"Gen capacity ingest complete. Total rows: {total}")


def incremental_gen_capacity(lookback_days: int = 35):
    """Load from the earliest not-yet-successfully-loaded day in the last
    `lookback_days` through yesterday.

    Scans pjm_ingest_log (per-day) rather than MAX(datetime_beginning_utc)
    ::date from the data table: a day's last EPT hour lands on the *next*
    UTC calendar date, so that approach overshoots by one day whenever the
    most recently loaded day is a Sunday — which, given this runs off a
    Monday cron, is every week — permanently skipping the Monday after it.
    """
    conn = psycopg2.connect(**DB)
    end_d = date.today() - timedelta(days=1)  # through yesterday

    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pjm_ingest_log WHERE feed LIKE %s LIMIT 1", (f"{LOG_FEED_KEY}_%",))
    has_any_data = cur.fetchone() is not None
    cur.close()

    if not has_any_data:
        conn.close()
        start = DEFAULT_START_DATE
        log.info(f"No existing data — starting from {start}")
        ingest_gen_capacity(start, end_d.isoformat())
        return

    start_d = None
    for i in range(lookback_days, -1, -1):
        d = end_d - timedelta(days=i)
        if get_last_loaded_date(conn, f"{LOG_FEED_KEY}_{d.isoformat()}") is None:
            start_d = d
            break
    conn.close()

    if start_d is None:
        log.info(f"Gen capacity already up to date through {end_d.isoformat()}")
        return

    ingest_gen_capacity(start_d.isoformat(), end_d.isoformat())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",       default=DEFAULT_START_DATE)
    parser.add_argument("--end",         default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--incremental", action="store_true")
    args = parser.parse_args()

    if args.incremental:
        incremental_gen_capacity()
    else:
        ingest_gen_capacity(args.start, args.end)

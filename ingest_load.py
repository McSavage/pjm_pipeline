"""
ingest_load.py — Ingest PJM hourly metered load by zone.

    python ingest_load.py --start 2022-01-01 --end 2024-12-31
    python ingest_load.py --incremental
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

FEED_KEY = "load_metered"


def insert_load_rows(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    cur = conn.cursor()
    sql = """
        INSERT INTO pjm_load_metered
            (datetime_beginning_utc, datetime_ending_utc, area, mw, company_verified)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (datetime_beginning_utc, area) DO NOTHING
    """
    inserted = 0
    for r in rows:
        # hrl_load_metered only returns datetime_beginning_utc — hourly feed,
        # so the interval ends exactly one hour later.
        beginning = r.get("datetime_beginning_utc")
        ending = r.get("datetime_ending_utc")
        if ending is None and beginning is not None:
            ending = (datetime.fromisoformat(beginning) + timedelta(hours=1)).isoformat()

        cur.execute(sql, (
            beginning,
            ending,
            r.get("area") or r.get("load_area"),
            r.get("mw")   or r.get("metered_mwh"),
            r.get("is_verified") in (True, "Y", "1", 1),
        ))
        inserted += 1
    conn.commit()
    cur.close()
    return inserted


def ingest_load(start_date: str, end_date: str):
    client = PJMClient()
    conn   = psycopg2.connect(**DB)

    current = datetime.fromisoformat(start_date).date()
    end     = datetime.fromisoformat(end_date).date()
    total   = 0

    while current <= end:
        day_str = current.strftime("%Y-%m-%d")
        log.info(f"Fetching load {day_str}...")
        params = {"datetime_beginning_ept": day_str}
        rows = client.fetch(FEEDS["load_metered"], params)
        time.sleep(client.delay)  # pace requests — daily response fits in one page
        n    = insert_load_rows(conn, rows)
        total += n
        log.info(f"  Inserted {n} rows")
        current += timedelta(days=1)

    conn.close()
    log.info(f"Load ingest complete. Total rows: {total}")


def incremental_load():
    conn = psycopg2.connect(**DB)
    cur  = conn.cursor()
    cur.execute("SELECT MAX(datetime_beginning_utc)::date FROM pjm_load_metered")
    row = cur.fetchone()
    cur.close()
    conn.close()

    last = row[0] if row and row[0] else None
    start = (last + timedelta(days=1)).isoformat() if last else DEFAULT_START_DATE
    end   = (date.today() - timedelta(days=1)).isoformat()

    if start > end:
        log.info("Load already up to date.")
        return
    ingest_load(start, end)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",       default=DEFAULT_START_DATE)
    parser.add_argument("--end",         default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--incremental", action="store_true")
    args = parser.parse_args()

    if args.incremental:
        incremental_load()
    else:
        ingest_load(args.start, args.end)

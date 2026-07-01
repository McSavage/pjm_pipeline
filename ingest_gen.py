"""
ingest_gen.py — Ingest PJM hourly generation by fuel type.

    python ingest_gen.py --start 2022-01-01 --end 2024-12-31
    python ingest_gen.py --incremental
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


def insert_gen_rows(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    cur = conn.cursor()
    sql = """
        INSERT INTO pjm_gen_by_fuel
            (datetime_beginning_utc, datetime_ending_utc, fuel_type, mw)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (datetime_beginning_utc, fuel_type) DO NOTHING
    """
    inserted = 0
    for r in rows:
        # gen_by_fuel only returns datetime_beginning_utc — hourly feed,
        # so the interval ends exactly one hour later.
        beginning = r.get("datetime_beginning_utc")
        ending = r.get("datetime_ending_utc")
        if ending is None and beginning is not None:
            ending = (datetime.fromisoformat(beginning) + timedelta(hours=1)).isoformat()

        cur.execute(sql, (
            beginning,
            ending,
            r.get("fuel_type") or r.get("fuel"),
            r.get("mw"),
        ))
        inserted += 1
    conn.commit()
    cur.close()
    return inserted


def ingest_gen(start_date: str, end_date: str):
    client = PJMClient()
    conn   = psycopg2.connect(**DB)

    current = datetime.fromisoformat(start_date).date()
    end     = datetime.fromisoformat(end_date).date()
    total   = 0

    while current <= end:
        day_str = current.strftime("%Y-%m-%d")
        log.info(f"Fetching gen_by_fuel {day_str}...")
        params = {"datetime_beginning_ept": day_str}
        rows = client.fetch(FEEDS["gen_by_fuel"], params)
        time.sleep(client.delay)  # pace requests — daily response fits in one page
        n    = insert_gen_rows(conn, rows)
        total += n
        log.info(f"  Inserted {n} rows")
        current += timedelta(days=1)

    conn.close()
    log.info(f"Gen ingest complete. Total rows: {total}")


def incremental_gen():
    conn = psycopg2.connect(**DB)
    cur  = conn.cursor()
    cur.execute("SELECT MAX(datetime_beginning_utc)::date FROM pjm_gen_by_fuel")
    row = cur.fetchone()
    cur.close()
    conn.close()

    last  = row[0] if row and row[0] else None
    start = (last + timedelta(days=1)).isoformat() if last else DEFAULT_START_DATE
    end   = (date.today() - timedelta(days=1)).isoformat()

    if start > end:
        log.info("Gen already up to date.")
        return
    ingest_gen(start, end)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",       default=DEFAULT_START_DATE)
    parser.add_argument("--end",         default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--incremental", action="store_true")
    args = parser.parse_args()

    if args.incremental:
        incremental_gen()
    else:
        ingest_gen(args.start, args.end)

"""
ingest_lmp.py — Ingest Day-Ahead and Real-Time hourly LMPs into PostgreSQL.

Modes:
    python ingest_lmp.py --feed da --start 2024-01-01 --end 2024-03-31
    python ingest_lmp.py --feed rt --start 2024-01-01 --end 2024-03-31
    python ingest_lmp.py --feed da --incremental        # loads since last loaded date
    python ingest_lmp.py --feed both --incremental      # both DA and RT

Scope:
    Default loads ZONE and HUB types only (manageable volume).
    Pass --full-nodal to load all bus locations (large — use with care).
"""

import argparse
import logging
import time
import psycopg2
from datetime import date, datetime, timedelta

from pjm_client import PJMClient
from config import DB, FEEDS, ZONES, HUBS, DEFAULT_START_DATE

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_last_loaded_date(conn, feed_key: str) -> date | None:
    # rows_inserted > 0 so a day that came back empty (e.g. verified RT data
    # not yet published by PJM) gets retried on the next run instead of being
    # marked done forever.
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


def insert_lmp_rows(conn, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    cur = conn.cursor()
    sql = f"""
        INSERT INTO {table}
            (datetime_beginning_utc, datetime_ending_utc,
             pnode_id, pnode_name, voltage, type,
             lmp, congestion_price, marginal_loss_price)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (datetime_beginning_utc, pnode_name) DO NOTHING
    """
    inserted = 0
    for r in rows:
        # Field names differ slightly between DA and RT feeds
        lmp_field  = "total_lmp_da"  if "da" in table else "total_lmp_rt"
        cong_field = "congestion_price_da" if "da" in table else "congestion_price_rt"
        loss_field = "marginal_loss_price_da" if "da" in table else "marginal_loss_price_rt"

        # da_hrl_lmps/rt_hrl_lmps only return datetime_beginning_utc — these are
        # hourly feeds, so the interval ends exactly one hour later.
        beginning = r.get("datetime_beginning_utc")
        ending = r.get("datetime_ending_utc")
        if ending is None and beginning is not None:
            ending = (datetime.fromisoformat(beginning) + timedelta(hours=1)).isoformat()

        cur.execute(sql, (
            beginning,
            ending,
            r.get("pnode_id"),
            r.get("pnode_name"),
            r.get("voltage"),
            r.get("type"),
            r.get(lmp_field)          or r.get("total_lmp"),
            r.get(cong_field)         or r.get("congestion_price"),
            r.get(loss_field)         or r.get("marginal_loss_price"),
        ))
        inserted += 1

    conn.commit()
    cur.close()
    return inserted


# ── Main ingest logic ─────────────────────────────────────────────────────────

def ingest_lmp(
    feed: str,            # "da" or "rt"
    start_date: str,
    end_date: str,
    full_nodal: bool = False,
):
    feed_key   = f"lmp_{feed}"
    api_feed   = FEEDS[f"{feed}_lmp"]
    table      = f"pjm_{feed}_lmp"

    client = PJMClient()
    conn   = psycopg2.connect(**DB)

    from datetime import datetime, timedelta
    current = datetime.fromisoformat(start_date).date()
    end     = datetime.fromisoformat(end_date).date()

    total_inserted = 0

    while current <= end:
        day_str = current.strftime("%Y-%m-%d")

        # Skip if already loaded
        existing = get_last_loaded_date(conn, f"{feed_key}_{day_str}")
        if existing:
            log.info(f"  {day_str} already loaded — skipping")
            current += timedelta(days=1)
            continue

        base_params = {
            "datetime_beginning_ept": day_str,
            "row_is_current": 1,
        }

        if full_nodal:
            log.info(f"Fetching {api_feed} {day_str} (all nodes)...")
            rows = client.fetch(api_feed, base_params)
            time.sleep(client.delay)
        else:
            log.info(f"Fetching {api_feed} {day_str} (ZONE + HUB)...")
            zone_rows = client.fetch(api_feed, {**base_params, "type": "ZONE"})
            time.sleep(client.delay)  # pace requests — daily responses fit in one page,
            hub_rows  = client.fetch(api_feed, {**base_params, "type": "HUB"})
            time.sleep(client.delay)  # so fetch()'s inter-page sleep never triggers here
            rows = zone_rows + hub_rows

        n = insert_lmp_rows(conn, table, rows)
        log_loaded_date(conn, f"{feed_key}_{day_str}", current, n)
        total_inserted += n
        log.info(f"  Inserted {n} rows for {day_str}")

        current += timedelta(days=1)

    conn.close()
    log.info(f"Done. Total rows inserted: {total_inserted}")


def incremental_ingest(feed: str, full_nodal: bool = False):
    """Load from the day after last loaded date through yesterday."""
    conn      = psycopg2.connect(**DB)
    feed_key  = f"lmp_{feed}"

    # Find the latest date we have any data for
    cur = conn.cursor()
    table = f"pjm_{feed}_lmp"
    cur.execute(f"SELECT MAX(datetime_beginning_utc)::date FROM {table}")
    row = cur.fetchone()
    cur.close()
    conn.close()

    last_date = row[0] if row and row[0] else None

    if last_date:
        start = (last_date + timedelta(days=1)).isoformat()
    else:
        start = DEFAULT_START_DATE
        log.info(f"No existing data — starting from {start}")

    end = (date.today() - timedelta(days=1)).isoformat()  # through yesterday

    if start > end:
        log.info(f"Already up to date through {end}")
        return

    log.info(f"Incremental {feed.upper()} LMP: {start} → {end}")
    ingest_lmp(feed, start, end, full_nodal=full_nodal)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from datetime import date, timedelta

    parser = argparse.ArgumentParser(description="Ingest PJM LMP data")
    parser.add_argument("--feed",        choices=["da", "rt", "both"], default="da")
    parser.add_argument("--start",       default=DEFAULT_START_DATE,
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--end",         default=(date.today() - timedelta(days=1)).isoformat(),
                        help="End date YYYY-MM-DD")
    parser.add_argument("--incremental", action="store_true",
                        help="Load from last loaded date through yesterday")
    parser.add_argument("--full-nodal",  action="store_true",
                        help="Load all bus nodes (large volume — use with care)")
    args = parser.parse_args()

    feeds = ["da", "rt"] if args.feed == "both" else [args.feed]

    for f in feeds:
        if args.incremental:
            incremental_ingest(f, full_nodal=args.full_nodal)
        else:
            ingest_lmp(f, args.start, args.end, full_nodal=args.full_nodal)

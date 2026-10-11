"""One-off repair: re-fetch days whose value columns are NULL (zeros dropped by the
old `a or b` fallback) and fill ONLY the NULL columns via COALESCE.

Run from the project dir:  .venv/bin/python repairs/2026-10-11_fill_null_zeros.py [load|da|rt ...]
Idempotent: only NULL columns are filled, so a re-run picks up where it stopped.

Ran 2026-10-11 after backing up to
pjm_backups/2026-10-11_pre_null_zero_fix/pjm_tables.dump. Every filled value was 0:
  pjm_da_lmp        congestion 4,649  loss 161  lmp 2
  pjm_rt_lmp        congestion 97,602 loss 13
  pjm_load_metered  mw 12 (AECO/DPLCO/PEPCO, spring-forward 1 AM EPT)
No NULLs remained afterwards.
"""
import sys, time, logging
from pathlib import Path
from collections import Counter
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psycopg2
import requests
from psycopg2.extras import execute_values
from config import DB, FEEDS
from pjm_client import PJMClient, first_present, parse_utc

log = logging.getLogger("repair")
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
logging.getLogger("pjm_client").setLevel(logging.WARNING)

# rows/page = 5000; ZONE 23 nodes x 25h = 575/day, HUB 12 x 25 = 300/day
SPAN_DAYS = {"ZONE": 8, "HUB": 16}

client = PJMClient()
conn = psycopg2.connect(**DB)
stats = Counter()


def affected_days(table, where):
    cur = conn.cursor()
    cur.execute(f"""SELECT DISTINCT (datetime_beginning_utc AT TIME ZONE 'America/New_York')::date
                    FROM {table} WHERE {where} ORDER BY 1""")
    days = [r[0] for r in cur.fetchall()]
    cur.close()
    return days


def chunk(days, span):
    out, start, cur_days = [], None, []
    for d in days:
        # PJM rejects ranges spanning a year (and likely the archive cutoff):
        # keep each range inside one calendar month.
        if start is None or (d - start).days >= span or (d.year, d.month) != (start.year, start.month):
            if cur_days:
                out.append((start, cur_days[-1]))
            start, cur_days = d, []
        cur_days.append(d)
    if cur_days:
        out.append((start, cur_days[-1]))
    return out


def apply_updates(table, keycol, cols, values):
    """values: list of (ts, key, *cols). Fills only NULL columns."""
    if not values:
        return 0
    cur = conn.cursor()
    cur.execute(f"CREATE TEMP TABLE IF NOT EXISTS fix_src (ts timestamptz, k text, "
                + ", ".join(f"{c} numeric" for c in cols) + ") ON COMMIT DROP")
    execute_values(cur, "INSERT INTO fix_src VALUES %s", values)
    sets = ", ".join(f"{c} = COALESCE(t.{c}, s.{c})" for c in cols)
    nulls = " OR ".join(f"(t.{c} IS NULL AND s.{c} IS NOT NULL)" for c in cols)
    # tally which values get filled (zero vs nonzero) before updating
    for c in cols:
        cur.execute(f"""SELECT count(*) FILTER (WHERE s.{c} = 0), count(*) FILTER (WHERE s.{c} <> 0)
                        FROM {table} t JOIN fix_src s ON t.datetime_beginning_utc = s.ts AND t.{keycol} = s.k
                        WHERE t.{c} IS NULL AND s.{c} IS NOT NULL""")
        z, nz = cur.fetchone()
        stats[f"{table}.{c}.zero"] += z
        stats[f"{table}.{c}.nonzero"] += nz
    cur.execute(f"""UPDATE {table} t SET {sets} FROM fix_src s
                    WHERE t.datetime_beginning_utc = s.ts AND t.{keycol} = s.k AND ({nulls})""")
    n = cur.rowcount
    conn.commit()
    cur.close()
    return n


def repair_lmp(feed):
    table = f"pjm_{feed}_lmp"
    cols = ["lmp", "congestion_price", "marginal_loss_price"]
    fields = [f"total_lmp_{feed}", f"congestion_price_{feed}", f"marginal_loss_price_{feed}"]
    fallbacks = ["total_lmp", "congestion_price", "marginal_loss_price"]
    for typ, span in SPAN_DAYS.items():
        days = affected_days(table, f"type = '{typ}' AND ("
                             + " OR ".join(f"{c} IS NULL" for c in cols) + ")")
        chunks = chunk(days, span)
        log.info(f"{table} {typ}: {len(days)} affected days -> {len(chunks)} requests")
        for i, (lo, hi) in enumerate(chunks, 1):
            params = {"datetime_beginning_ept": f"{lo} 00:00 to {hi} 23:59",
                      "row_is_current": 1, "type": typ}
            try:
                rows = client.fetch(FEEDS[f"{feed}_lmp"], params)
                time.sleep(client.delay)
            except requests.HTTPError as e:
                log.warning(f"  range {lo}..{hi} rejected ({e}); falling back to per-day")
                time.sleep(client.delay)
                rows, d = [], lo
                while d <= hi:
                    rows += client.fetch(FEEDS[f"{feed}_lmp"], {**params, "datetime_beginning_ept": d.isoformat()})
                    time.sleep(client.delay)
                    d += timedelta(days=1)
            keys = [(r["datetime_beginning_utc"], r["pnode_name"]) for r in rows]
            if len(keys) != len(set(keys)):
                raise RuntimeError(f"duplicate keys in {table} {typ} {lo}..{hi}")
            if len(rows) >= 5000:
                stats["warn_paged"] += 1
            values = [(parse_utc(r["datetime_beginning_utc"]), r["pnode_name"],
                       *(first_present(r, f, fb) for f, fb in zip(fields, fallbacks)))
                      for r in rows]
            n = apply_updates(table, "pnode_name", cols, values)
            stats[f"{table}.rows_updated"] += n
            log.info(f"  [{i}/{len(chunks)}] {typ} {lo}..{hi}: fetched {len(rows)}, updated {n}")


def repair_load():
    table = "pjm_load_metered"
    for d in affected_days(table, "mw IS NULL"):
        rows = client.fetch(FEEDS["load_metered"], {"datetime_beginning_ept": d.isoformat()})
        time.sleep(client.delay)
        values = [(parse_utc(r["datetime_beginning_utc"]), first_present(r, "area", "load_area"),
                   first_present(r, "mw", "metered_mwh")) for r in rows]
        n = apply_updates(table, "area", ["mw"], values)
        stats[f"{table}.rows_updated"] += n
        log.info(f"  load {d}: fetched {len(rows)}, updated {n}")


if __name__ == "__main__":
    which = sys.argv[1:] or ["load", "da", "rt"]
    for w in which:
        repair_load() if w == "load" else repair_lmp(w)
    conn.close()
    for k, v in sorted(stats.items()):
        log.info(f"STAT {k} = {v}")

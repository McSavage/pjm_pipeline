"""
ingest_pnodes.py — Load pricing node master table.
Run once (or quarterly to pick up new/retired nodes).

    python ingest_pnodes.py
"""

import psycopg2
import logging
from pjm_client import PJMClient
from config import DB, FEEDS

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")


def load_pnodes():
    client = PJMClient()
    log.info("Fetching pnode master...")
    rows = client.fetch(FEEDS["pnodes"], {})
    log.info(f"Fetched {len(rows)} pnodes")

    conn = psycopg2.connect(**DB)
    cur  = conn.cursor()

    upsert_sql = """
        INSERT INTO pjm_pnodes
            (pnode_id, pnode_name, pnode_type, voltage_level, zone,
             sub_zone, effective_date, termination_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (pnode_id, pnode_name) DO UPDATE SET
            pnode_type       = EXCLUDED.pnode_type,
            voltage_level    = EXCLUDED.voltage_level,
            zone             = EXCLUDED.zone,
            sub_zone         = EXCLUDED.sub_zone,
            effective_date   = EXCLUDED.effective_date,
            termination_date = EXCLUDED.termination_date
    """

    inserted = 0
    for r in rows:
        cur.execute(upsert_sql, (
            r.get("pnode_id"),
            r.get("pnode_name"),
            r.get("pnode_type"),
            r.get("voltage_level"),
            r.get("zone"),
            r.get("sub_zone"),
            r.get("effective_date"),
            r.get("termination_date"),
        ))
        inserted += 1

    conn.commit()
    cur.close()
    conn.close()
    log.info(f"Upserted {inserted} pnodes.")


if __name__ == "__main__":
    load_pnodes()

"""
install_queries.py — Install saved query functions in PostgreSQL.
Run once, and again after editing queries.sql:  python install_queries.py

Safe to re-run: queries.sql uses CREATE OR REPLACE.
"""

import logging
import psycopg2
from config import DB

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger(__name__)


def install():
    with open("queries.sql") as f:
        sql = f.read()

    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor()

    log.info("Installing saved queries...")
    cur.execute(sql)
    log.info("Saved queries installed.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    install()

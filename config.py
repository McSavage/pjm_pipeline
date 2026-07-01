"""
config.py — Central configuration for PJM Pipeline.
Edit ZONES and START_DATE to control scope.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Database ──────────────────────────────────────────────────────────────────

DB = {
    "dbname":   os.getenv("POSTGRES_DB",       "pjm_pipeline"),
    "user":     os.getenv("POSTGRES_USER",     "pjm_user"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
    "host":     os.getenv("POSTGRES_HOST",     "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", "5432")),
}

# ── PJM API ───────────────────────────────────────────────────────────────────

PJM_API_KEY  = os.getenv("PJM_API_KEY", "")
PJM_BASE_URL = "https://api.pjm.com/api/v1"

# Non-member rate limit: 6 requests/minute → sleep 11s between calls to be safe
REQUEST_DELAY_SECONDS = 11.0
PAGE_SIZE             = 5000   # max rows per API response page

# ── Scope ─────────────────────────────────────────────────────────────────────

# Transmission zones relevant to the datacenter story.
# DOMINION = Northern Virginia (Amazon, Microsoft hyperscale cluster)
# AEP      = Ohio / West Virginia datacenter corridor
# COMED    = ComEd Chicago area
# PECO     = Philadelphia area
# Add more as needed: PSEG, BGE, PPL, DUQUESNE, ATSI, DEOK, DAYTON, EKPC
ZONES = [
    "DOM",
    "AEP",
    "COMED",
    "PECO",
]

# Hub aggregates — useful price benchmarks
HUBS = [
    "AEP-DAYTON HUB",
    "WESTERN HUB",
    "EASTERN HUB",
    "NEW JERSEY HUB",
]

# Default historical backfill start.
# PJM DA LMP data available from ~2008; 2022 captures the capacity price run-up.
DEFAULT_START_DATE = "2022-01-01"

# ── Feed names ────────────────────────────────────────────────────────────────

FEEDS = {
    "pnodes":       "pnode",
    "da_lmp":       "da_hrl_lmps",
    "rt_lmp":       "rt_hrl_lmps",
    "load_metered": "hrl_load_metered",
    "gen_by_fuel":  "gen_by_fuel",
    "gen_capacity": "day_gen_capacity",
    "rt_5min_lmp":  "rt_fivemin_hrl_lmps",   # granular, use sparingly
}

"""Quick smoke test — fetches one day of DA LMP data to verify API key and connectivity."""

from pjm_client import PJMClient

client = PJMClient()

rows = client.fetch("da_hrl_lmps", {
    "datetime_beginning_ept": "2026-04-01",
    "row_is_current": 1,
    "type": "ZONE",
    "rowCount": 5,
})

if rows:
    print(f"OK — got {len(rows)} row(s)")
    print(rows[0])
else:
    print("Connected but no rows returned")

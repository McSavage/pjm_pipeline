"""
pjm_client.py — PJM DataMiner2 API client.

Handles:
  - Subscription key auth
  - Automatic pagination (5000-row pages)
  - Non-member rate limiting (6 req/min → 11s sleep)
  - Retry on transient errors

Usage:
    from pjm_client import PJMClient
    client = PJMClient()
    rows = client.fetch("da_hrl_lmps", {"datetime_beginning_ept": "2024-01-01 00:00",
                                         "datetime_ending_ept":   "2024-01-01 23:59",
                                         "type": "ZONE"})
"""

import logging
import time
from typing import Any

import requests

from config import PAGE_SIZE, PJM_API_KEY, PJM_BASE_URL, REQUEST_DELAY_SECONDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


class PJMClient:
    def __init__(self, api_key: str = PJM_API_KEY, delay: float = REQUEST_DELAY_SECONDS):
        if not api_key:
            raise ValueError("PJM_API_KEY not set — check your .env file")
        self.headers = {
            "Ocp-Apim-Subscription-Key": api_key,
            "Accept": "application/json",
        }
        self.delay   = delay
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    # ── Public ────────────────────────────────────────────────────────────────

    def fetch(self, feed: str, params: dict[str, Any], max_retries: int = 3) -> list[dict]:
        """
        Fetch all rows from a DataMiner2 feed for the given params.
        Paginates automatically. Sleeps between pages to respect rate limit.
        Returns a flat list of row dicts.
        """
        url  = f"{PJM_BASE_URL}/{feed}"
        rows = []
        page_params = {**params, "rowCount": PAGE_SIZE, "startRow": 1}

        while True:
            batch = self._get_with_retry(url, page_params, max_retries)
            rows.extend(batch)
            log.info(f"  {feed}: fetched {len(rows)} rows so far (page size {len(batch)})")

            if len(batch) < PAGE_SIZE:
                break  # last page
            page_params["startRow"] += PAGE_SIZE
            time.sleep(self.delay)

        return rows

    def fetch_date_range(
        self,
        feed: str,
        start_date: str,
        end_date: str,
        extra_params: dict[str, Any] | None = None,
        chunk: str = "day",
    ) -> list[dict]:
        """
        Convenience wrapper: iterate over a date range, one chunk at a time.
        chunk='day'  → one request per calendar day  (recommended for LMPs)
        chunk='month'→ one request per month          (ok for load/gen)
        """
        from datetime import datetime, timedelta

        extra = extra_params or {}
        rows  = []
        start = datetime.fromisoformat(start_date)
        end   = datetime.fromisoformat(end_date)

        current = start
        while current <= end:
            if chunk == "day":
                fmt_start = current.strftime("%Y-%m-%d")
                step      = timedelta(days=1)
            elif chunk == "month":
                import calendar
                last_day  = calendar.monthrange(current.year, current.month)[1]
                fmt_start = current.strftime("%Y-%m-01")
                step      = timedelta(days=last_day)
            else:
                raise ValueError(f"Unknown chunk: {chunk!r} — use 'day' or 'month'")

            log.info(f"Fetching {feed} {fmt_start}")
            params = {
                "datetime_beginning_ept": fmt_start,
                **extra,
            }
            batch = self.fetch(feed, params)
            rows.extend(batch)

            current += step
            if current <= end:
                time.sleep(self.delay)  # rate limit between chunks

        return rows

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_with_retry(self, url: str, params: dict, max_retries: int) -> list[dict]:
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                # DataMiner2 wraps rows in {"items": [...], "totalRows": N}
                return data.get("items", data if isinstance(data, list) else [])
            except requests.HTTPError as e:
                status = e.response.status_code if e.response else "?"
                if status == 429:
                    wait = self.delay * attempt * 2
                    log.warning(f"Rate limited (429) — waiting {wait:.0f}s (attempt {attempt})")
                    time.sleep(wait)
                elif status in (500, 502, 503, 504):
                    wait = self.delay * attempt
                    log.warning(f"Server error {status} — retrying in {wait:.0f}s (attempt {attempt})")
                    time.sleep(wait)
                else:
                    log.error(f"HTTP {status} on {url} — {e}")
                    raise
            except requests.RequestException as e:
                log.warning(f"Request error on attempt {attempt}: {e}")
                if attempt == max_retries:
                    raise
                time.sleep(self.delay * attempt)

        raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts")

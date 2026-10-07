"""CollegeFootballData API client with an on-disk cache and polite rate limiting.

Free tier = 1,000 calls/month (shared with basketball). Every response is cached as gzip JSON;
callers decide when a cached response is still good (`fresh` callback or `max_age_hours`).
"""
import gzip
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "cfbd"
CACHE.mkdir(parents=True, exist_ok=True)
BASE = "https://api.collegefootballdata.com"
MIN_INTERVAL = 1.0  # seconds between live calls
STATE = {"last": 0.0, "calls": 0, "remaining": None}


def _key():
    k = os.environ.get("CFBD_API_KEY")
    if not k:
        env = ROOT / ".env"
        for line in env.read_text().splitlines() if env.exists() else []:
            if line.startswith("CFBD_API_KEY="):
                k = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not k:
        raise RuntimeError("CFBD_API_KEY not set in .env")
    return k


def _path(name):
    return CACHE / f"{name}.json.gz"


def cached_at(name):
    p = _path(name)
    if not p.exists():
        return None
    with gzip.open(p, "rt") as f:
        return datetime.fromisoformat(json.load(f)["fetched_at"])


def get(endpoint, params, name, max_age_hours=None, fresh=None):
    """Return JSON for endpoint+params. Uses the cache unless it is missing or stale.

    max_age_hours=None and fresh=None -> cache forever (completed data).
    fresh(fetched_at: datetime) -> bool lets the caller decide.
    """
    p = _path(name)
    if p.exists():
        with gzip.open(p, "rt") as f:
            blob = json.load(f)
        fetched = datetime.fromisoformat(blob["fetched_at"])
        age_h = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
        ok = True
        if max_age_hours is not None and age_h > max_age_hours:
            ok = False
        if fresh is not None and not fresh(fetched):
            ok = False
        if ok:
            return blob["data"]
    wait = MIN_INTERVAL - (time.time() - STATE["last"])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(4):
        r = requests.get(BASE + endpoint, params=params, timeout=60,
                         headers={"Authorization": f"Bearer {_key()}", "Accept": "application/json"})
        STATE["last"] = time.time()
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        break
    r.raise_for_status()
    STATE["calls"] += 1
    STATE["remaining"] = r.headers.get("x-calllimit-remaining", STATE["remaining"])
    (CACHE / "_usage.json").write_text(json.dumps({"remaining": STATE["remaining"],
                                                   "at": datetime.now(timezone.utc).isoformat()}))
    data = r.json()
    tmp = p.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as f:
        json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(), "params": params, "data": data}, f)
    os.replace(tmp, p)
    return data


def usage_line():
    rem = STATE["remaining"]
    if rem is None and (CACHE / "_usage.json").exists():
        rem = json.loads((CACHE / "_usage.json").read_text())["remaining"]
    return f"CFBD: {STATE['calls']} live call(s) this run, {rem or '?'} remaining this month"

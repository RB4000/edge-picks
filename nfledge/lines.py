"""Market lines: The Odds API (primary), ESPN public scoreboard JSON (fallback), on-disk cache (last resort).

Every source is normalised to one row per game:
    game_id, home_spread (betting notation, home side), total, source, fetched_at (UTC ISO)
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import config
from nfledge.teams import ESPN_TO_ABBR, FULL_TO_ABBR

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "lines"
CACHE.mkdir(parents=True, exist_ok=True)
TIMEOUT = 20


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _half(x):
    return float(np.round(x * 2) / 2)


def _match(schedule, home, away, when_utc):
    """Find the schedule game_id for a home/away pair within 3 days of the listed start time."""
    cand = schedule[(schedule["home_team"] == home) & (schedule["away_team"] == away)]
    if cand.empty:
        return None
    days = (pd.to_datetime(cand["gameday"]).dt.tz_localize("UTC") - pd.Timestamp(when_utc)).abs()
    best = days.idxmin()
    return cand.loc[best, "game_id"] if days.loc[best] <= pd.Timedelta(days=3) else None


# --- The Odds API --------------------------------------------------------------

def _odds_api_raw():
    """Returns (payload, fetched_at). Reuses a recent response to conserve free-tier credits."""
    latest = CACHE / "odds_api_latest.json"
    if latest.exists():
        cached = json.loads(latest.read_text())
        age = _now() - datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))
        if age < timedelta(hours=config.ODDS_API_MIN_REFRESH_HOURS):
            return cached["payload"], cached["fetched_at"]
    _load_env()
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise RuntimeError("ODDS_API_KEY not set")
    r = requests.get(
        f"https://api.the-odds-api.com/v4/sports/{config.ODDS_API_SPORT}/odds",
        params={"regions": "us", "markets": "spreads,totals", "oddsFormat": "american", "apiKey": key},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    fetched_at = _iso(_now())
    latest.write_text(json.dumps({"fetched_at": fetched_at, "payload": r.json(),
                                  "remaining": r.headers.get("x-requests-remaining")}))
    return r.json(), fetched_at


def from_odds_api(schedule):
    payload, fetched_at = _odds_api_raw()
    return _parse_odds_payload(schedule, payload, fetched_at, "The Odds API")


def _parse_odds_payload(schedule, payload, fetched_at, label):
    rows = []
    for ev in payload:
        home, away = FULL_TO_ABBR.get(ev["home_team"]), FULL_TO_ABBR.get(ev["away_team"])
        if not home or not away:
            continue
        gid = _match(schedule, home, away, ev["commence_time"])
        if not gid:
            continue
        spreads, totals = [], []
        for bk in ev.get("bookmakers", []):
            for m in bk.get("markets", []):
                if m["key"] == "spreads":
                    spreads += [o["point"] for o in m["outcomes"] if o["name"] == ev["home_team"] and "point" in o]
                elif m["key"] == "totals":
                    totals += [o["point"] for o in m["outcomes"] if o["name"] == "Over" and "point" in o]
        if spreads and totals:
            rows.append({
                "game_id": gid, "home_spread": _half(np.median(spreads)), "total": _half(np.median(totals)),
                "source": f"{label} · median of {len(spreads)} US books", "fetched_at": fetched_at,
            })
    return pd.DataFrame(rows)


# --- ESPN fallback --------------------------------------------------------------

def from_espn(schedule, season, week):
    seasontype, espn_week = (2, week) if week <= 18 else (3, {19: 1, 20: 2, 21: 3, 22: 5}.get(week, week - 18))
    r = requests.get(
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        params={"seasontype": seasontype, "week": espn_week, "dates": season}, timeout=TIMEOUT,
    )
    r.raise_for_status()
    fetched_at = _iso(_now())
    (CACHE / f"espn_{season}_{week:02d}.json").write_text(json.dumps({"fetched_at": fetched_at, "payload": r.json()}))
    rows = []
    for ev in r.json().get("events", []):
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: ESPN_TO_ABBR.get(c["team"]["abbreviation"]) for c in comp["competitors"]}
        odds = (comp.get("odds") or [{}])[0]
        if odds.get("spread") is None or odds.get("overUnder") is None:
            continue
        gid = _match(schedule, teams.get("home"), teams.get("away"), ev["date"])
        if gid:
            provider = odds.get("provider", {}).get("name", "ESPN")
            rows.append({"game_id": gid, "home_spread": float(odds["spread"]), "total": float(odds["overUnder"]),
                         "source": f"ESPN ({provider})", "fetched_at": fetched_at})
    return pd.DataFrame(rows)


def from_cache(schedule):
    """Last successful Odds API payload, regardless of age. Lines keep their original fetch time."""
    latest = CACHE / "odds_api_latest.json"
    if not latest.exists():
        return pd.DataFrame()
    cached = json.loads(latest.read_text())
    return _parse_odds_payload(schedule, cached["payload"], cached["fetched_at"], "The Odds API (cached)")


def fetch(schedule, season, week, log=print):
    """Try each source in order; fill gaps from later sources. Returns one row per game_id."""
    frames = []
    for name, fn in (("odds_api", lambda: from_odds_api(schedule)),
                     ("espn", lambda: from_espn(schedule, season, week)),
                     ("cache", lambda: from_cache(schedule))):
        try:
            df = fn()
            log(f"  lines: {name} -> {len(df)} games")
            frames.append(df)
        except Exception as e:  # network/API failures fall through to the next source
            log(f"  lines: {name} unavailable ({type(e).__name__}: {e})")
        have = set(pd.concat(frames)["game_id"]) if frames else set()
        wk = set(schedule[(schedule["season"] == season) & (schedule["week"] == week)]["game_id"])
        if wk and wk <= have:
            break
    if not frames:
        return pd.DataFrame(columns=["game_id", "home_spread", "total", "source", "fetched_at"])
    return pd.concat(frames, ignore_index=True).drop_duplicates("game_id", keep="first")

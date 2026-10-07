"""Market lines: The Odds API (primary), ESPN public scoreboard JSON (fallback), on-disk cache (last resort).

Every source is normalised to one row per game:
    game_id, home_spread (betting notation, home side), total, source, fetched_at (UTC ISO)

A `Feed` describes one sport: which Odds API sport key, how to map feed team names to our team
keys, and which ESPN scoreboard to fall back to.
"""
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import requests

import config
from nfledge.teams import ESPN_TO_ABBR, FULL_TO_ABBR

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "lines"
CACHE.mkdir(parents=True, exist_ok=True)
USAGE_LOG = ROOT / "data" / "logs" / "odds_api_usage.csv"
TIMEOUT = 20


@dataclass
class Feed:
    key: str
    odds_sport: str
    cache_file: str
    resolve: Callable          # Odds API full team name -> team key (or None)
    espn_url: str
    espn_params: Callable      # (season, week) -> query params
    espn_team: Callable        # ESPN competitor["team"] dict -> team key (or None)


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
    """(game_id, swapped) for a home/away pair within 3 days of the listed start time.
    Neutral-site games are sometimes listed with home/away reversed; then swapped=True."""
    for h, a, swapped in ((home, away, False), (away, home, True)):
        cand = schedule[(schedule["home_team"] == h) & (schedule["away_team"] == a)]
        if cand.empty:
            continue
        days = (pd.to_datetime(cand["gameday"]).dt.tz_localize("UTC") - pd.Timestamp(when_utc)).abs()
        best = days.idxmin()
        if days.loc[best] <= pd.Timedelta(days=3):
            return cand.loc[best, "game_id"], swapped
    return None, False


# --- The Odds API --------------------------------------------------------------

def odds_usage():
    """Most recent Odds API usage from response headers: (used, remaining) this billing month."""
    if not USAGE_LOG.exists():
        return None
    last = pd.read_csv(USAGE_LOG).dropna(subset=["used", "remaining"]).iloc[-1]
    return int(last["used"]), int(last["remaining"])


def _log_usage(feed, headers):
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    new = not USAGE_LOG.exists()
    with open(USAGE_LOG, "a") as f:
        if new:
            f.write("fetched_at,sport,last_cost,used,remaining\n")
        f.write(f"{_iso(_now())},{feed.key},{headers.get('x-requests-last', '')},"
                f"{headers.get('x-requests-used', '')},{headers.get('x-requests-remaining', '')}\n")


def _odds_api_raw(feed, not_before=None):
    """Returns (payload, fetched_at). Reuses a recent response to conserve free-tier credits,
    unless it was fetched before `not_before` (e.g. before the Tuesday freeze window opened)."""
    latest = CACHE / feed.cache_file
    if latest.exists():
        cached = json.loads(latest.read_text())
        fetched = datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))
        fresh_enough = not_before is None or fetched >= not_before
        if _now() - fetched < timedelta(hours=config.ODDS_API_MIN_REFRESH_HOURS) and fresh_enough:
            return cached["payload"], cached["fetched_at"]
    _load_env()
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise RuntimeError("ODDS_API_KEY not set")
    r = requests.get(
        f"https://api.the-odds-api.com/v4/sports/{feed.odds_sport}/odds",
        params={"regions": "us", "markets": "spreads,totals", "oddsFormat": "american", "apiKey": key},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    _log_usage(feed, r.headers)
    fetched_at = _iso(_now())
    latest.write_text(json.dumps({"fetched_at": fetched_at, "payload": r.json(),
                                  "remaining": r.headers.get("x-requests-remaining"),
                                  "used": r.headers.get("x-requests-used")}))
    return r.json(), fetched_at


def from_odds_api(feed, schedule, not_before=None):
    payload, fetched_at = _odds_api_raw(feed, not_before)
    return _parse_odds_payload(feed, schedule, payload, fetched_at, "The Odds API")


def _parse_odds_payload(feed, schedule, payload, fetched_at, label):
    rows, unmatched = [], set()
    for ev in payload:
        home, away = feed.resolve(ev["home_team"]), feed.resolve(ev["away_team"])
        if not home or not away:
            unmatched |= {n for n, t in ((ev["home_team"], home), (ev["away_team"], away)) if not t}
            continue
        gid, swapped = _match(schedule, home, away, ev["commence_time"])
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
            sp = _half(np.median(spreads))
            rows.append({
                "game_id": gid, "home_spread": -sp if swapped else sp, "total": _half(np.median(totals)),
                "source": f"{label} · median of {len(spreads)} US books", "fetched_at": fetched_at,
            })
    df = pd.DataFrame(rows)
    df.attrs["unmatched"] = sorted(unmatched)
    return df


# --- ESPN fallback --------------------------------------------------------------

def from_espn(feed, schedule, season, week):
    r = requests.get(feed.espn_url, params=feed.espn_params(season, week), timeout=TIMEOUT)
    r.raise_for_status()
    fetched_at = _iso(_now())
    (CACHE / f"espn_{feed.key}_{season}_{week:02d}.json").write_text(
        json.dumps({"fetched_at": fetched_at, "payload": r.json()}))
    rows = []
    for ev in r.json().get("events", []):
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: feed.espn_team(c["team"]) for c in comp["competitors"]}
        odds = (comp.get("odds") or [{}])[0]
        if odds.get("spread") is None or odds.get("overUnder") is None:
            continue
        gid, swapped = _match(schedule, teams.get("home"), teams.get("away"), ev["date"])
        if gid:
            provider = odds.get("provider", {}).get("name", "ESPN")
            sp = float(odds["spread"])
            rows.append({"game_id": gid, "home_spread": -sp if swapped else sp, "total": float(odds["overUnder"]),
                         "source": f"ESPN ({provider})", "fetched_at": fetched_at})
    return pd.DataFrame(rows)


def from_cache(feed, schedule):
    """Last successful Odds API payload, regardless of age. Lines keep their original fetch time."""
    latest = CACHE / feed.cache_file
    if not latest.exists():
        return pd.DataFrame()
    cached = json.loads(latest.read_text())
    return _parse_odds_payload(feed, schedule, cached["payload"], cached["fetched_at"], "The Odds API (cached)")


def fetch(feed, schedule, season, week, log=print, not_before=None, want=None):
    """Try each source in order; fill gaps from later sources. Returns one row per game_id.
    `want`: game_ids we need; stop early once all are covered."""
    frames = []
    for name, fn in (("odds_api", lambda: from_odds_api(feed, schedule, not_before)),
                     ("espn", lambda: from_espn(feed, schedule, season, week)),
                     ("cache", lambda: from_cache(feed, schedule))):
        try:
            df = fn()
            msg = f"  {feed.key} lines: {name} -> {len(df)} games"
            if df.attrs.get("unmatched"):
                msg += f" ({len(df.attrs['unmatched'])} unmatched names, e.g. {df.attrs['unmatched'][:3]})"
            log(msg)
            frames.append(df)
        except Exception as e:  # network/API failures fall through to the next source
            log(f"  {feed.key} lines: {name} unavailable ({type(e).__name__}: {e})")
        have = set(pd.concat(frames)["game_id"]) if any(len(f) for f in frames) else set()
        if want is not None and set(want) <= have:
            break
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame(columns=["game_id", "home_spread", "total", "source", "fetched_at"])
    return pd.concat(frames, ignore_index=True).drop_duplicates("game_id", keep="first")


# --- feeds -------------------------------------------------------------------------

def nfl_feed():
    return Feed(
        key="nfl", odds_sport=config.ODDS_API_SPORT, cache_file="odds_api_latest.json",
        resolve=FULL_TO_ABBR.get,
        espn_url="https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        espn_params=lambda season, week: (
            {"seasontype": 2, "week": week, "dates": season} if week <= 18 else
            {"seasontype": 3, "week": {19: 1, 20: 2, 21: 3, 22: 5}.get(week, week - 18), "dates": season}),
        espn_team=lambda t: ESPN_TO_ABBR.get(t.get("abbreviation")),
    )


def ncaaf_feed(teams):
    from nfledge.cfb_data import name_resolver
    by_id = {str(int(r.id)): school for school, r in teams.iterrows()}
    return Feed(
        key="ncaaf", odds_sport=config.NCAAF_ODDS_API_SPORT, cache_file="odds_api_latest_ncaaf.json",
        resolve=name_resolver(teams),
        espn_url="https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
        espn_params=lambda season, week: {"groups": 80, "seasontype": 2, "week": week, "dates": season, "limit": 400},
        espn_team=lambda t: by_id.get(str(t.get("id"))),
    )

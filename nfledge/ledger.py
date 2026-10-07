"""The public ledger. Append-only CSV/JSON files in ./ledger, committed to git.

  line_snapshots.csv   every line observed for every upcoming game, every run
  frozen_lines.csv     the FIRST line observed per game; written once, never changed
  picks.csv            one row per game, written once before kickoff, never changed
  closing_lines.csv    the closing line per game, written once the game is final
  ratings/S_wWW.json   the full rating fit used to make that week's picks

Nothing here is edited by hand. Rows are only ever appended; existing game_ids are skipped.
"""
import csv
import json
import os
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "ledger"
RATINGS = LEDGER / "ratings"

FILES = {
    "line_snapshots": ["game_id", "home_spread", "total", "source", "fetched_at", "run_at"],
    "frozen_lines": ["game_id", "home_spread", "total", "source", "fetched_at", "frozen_at"],
    "picks": [
        "game_id", "season", "week", "kickoff_utc", "away", "home", "locked_at",
        "line_home_spread", "line_total", "line_source", "line_fetched_at",
        "model_away_pts", "model_home_pts", "model_home_spread", "model_total",
        "spread_pick", "spread_pick_line", "spread_edge", "spread_tier",
        "total_pick", "total_edge", "total_tier", "ratings_file", "model_version",
    ],
    "closing_lines": ["game_id", "close_home_spread", "close_total", "close_source", "recorded_at"],
}


def path(name):
    return LEDGER / f"{name}.csv"


def read(name):
    p = path(name)
    if not p.exists():
        return pd.DataFrame(columns=FILES[name])
    return pd.read_csv(p, dtype={"game_id": str})


def append(name, rows, unique=True):
    """Append rows. With unique=True, rows whose game_id already exists are silently skipped."""
    if not rows:
        return 0
    LEDGER.mkdir(exist_ok=True)
    existing = set(read(name)["game_id"]) if unique else set()
    rows = [r for r in rows if not unique or r["game_id"] not in existing]
    if not rows:
        return 0
    p = path(name)
    new = not p.exists()
    with open(p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FILES[name], extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)
        f.flush()
        os.fsync(f.fileno())
    return len(rows)


def ratings_path(season, week):
    return RATINGS / f"{season}_w{week:02d}.json"


def write_ratings_once(season, week, payload):
    p = ratings_path(season, week)
    if p.exists():
        return p
    RATINGS.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=RATINGS, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f, indent=1)
    os.replace(tmp, p)
    return p


def read_ratings(season, week):
    p = ratings_path(season, week)
    return json.loads(p.read_text()) if p.exists() else None

"""The public ledger. Append-only CSV/JSON files, committed to git.

NFL lives in ./ledger (its original location, unchanged since launch); NCAAF in ./ledger/ncaaf.

  line_snapshots.csv   every line observed for every upcoming game, every run
  frozen_lines.csv     the line each pick is graded on; written once, never changed
  picks.csv            one row per game, written once before kickoff, never changed
  closing_lines.csv    the closing line per game, written once the game is final
  ratings/S_wWW.json   the full rating fit used to make that week's picks
  graded.csv           derived on every run from the files above + final scores (not hand-edited)

Rows are only ever appended; existing game_ids are skipped.
"""
import csv
import json
import os
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

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


class Ledger:
    def __init__(self, root: Path, public_prefix: str):
        self.root = Path(root)
        self.ratings_dir = self.root / "ratings"
        self.public_prefix = public_prefix  # where the files are served on the site, e.g. "ledger/ncaaf/"

    def path(self, name):
        return self.root / f"{name}.csv"

    def read(self, name):
        p = self.path(name)
        if not p.exists():
            return pd.DataFrame(columns=FILES[name])
        return pd.read_csv(p, dtype={"game_id": str})

    def append(self, name, rows, unique=True):
        """Append rows. With unique=True, rows whose game_id already exists are silently skipped."""
        if not rows:
            return 0
        self.root.mkdir(parents=True, exist_ok=True)
        existing = set(self.read(name)["game_id"]) if unique else set()
        rows = [r for r in rows if not unique or str(r["game_id"]) not in existing]
        if not rows:
            return 0
        p = self.path(name)
        new = not p.exists()
        with open(p, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FILES[name], extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        return len(rows)

    def ratings_path(self, season, week):
        return self.ratings_dir / f"{season}_w{week:02d}.json"

    def write_ratings_once(self, season, week, payload):
        p = self.ratings_path(season, week)
        if p.exists():
            return p
        self.ratings_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.ratings_dir, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=1)
        os.replace(tmp, p)
        return p

    def read_ratings(self, season, week):
        p = self.ratings_path(season, week)
        return json.loads(p.read_text()) if p.exists() else None

    def public_files(self):
        return sorted(self.root.glob("*.csv"))


NFL = Ledger(ROOT / "ledger", "ledger/")
NCAAF = Ledger(ROOT / "ledger" / "ncaaf", "ledger/ncaaf/")

# Backwards-compatible module-level API (NFL).
LEDGER, RATINGS = NFL.root, NFL.ratings_dir
read, append, path = NFL.read, NFL.append, NFL.path
ratings_path, write_ratings_once, read_ratings = NFL.ratings_path, NFL.write_ratings_once, NFL.read_ratings

"""nflverse data access (via nflreadpy, the maintained successor to nfl_data_py).

Raw play-by-play is reduced to one row per team-game offense and cached as parquet.
Completed seasons are cached permanently; the current season is refreshed on each run.
"""
from pathlib import Path

import numpy as np
import pandas as pd

import config

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

PBP_COLS = [
    "game_id", "season", "week", "season_type", "posteam", "defteam", "home_team", "away_team",
    "play_type", "epa", "wp", "qtr", "drive", "game_seconds_remaining", "score_differential", "play_id",
]


def load_schedules(refresh=True):
    path = CACHE / "schedules.parquet"
    if refresh or not path.exists():
        import nflreadpy as nfl
        seasons = list(range(config.FIRST_SEASON, config.CURRENT_SEASON + 1))
        df = nfl.load_schedules(seasons).to_pandas()
        df.to_parquet(path)
    df = pd.read_parquet(path)
    df["neutral_nflverse"] = df["location"].eq("Neutral")
    intl = df["stadium_id"].isin(config.INTERNATIONAL_STADIUM_IDS) | df["stadium"].fillna("").str.contains(
        "|".join(config.INTERNATIONAL_VENUE_WORDS), case=False)
    df["international"] = intl
    df["neutral_venue"] = df["neutral_nflverse"] | intl
    for gid, flag in config.NEUTRAL_OVERRIDES.items():
        df.loc[df["game_id"] == gid, "neutral_venue"] = flag
    df["neutral"] = df["neutral_venue"] if config.NEUTRAL_RULE == "venue" else df["neutral_nflverse"]
    # Betting convention: home_spread -3.5 means home favored by 3.5. nflverse spread_line is the reverse.
    df["close_home_spread"] = -df["spread_line"]
    df["close_total"] = df["total_line"]
    df["final"] = df["home_score"].notna() & df["away_score"].notna()
    return df


def _team_games_for_season(season):
    import nflreadpy as nfl
    pbp = nfl.load_pbp([season]).select(PBP_COLS).to_pandas()
    pbp = pbp[pbp["play_type"].isin(["pass", "run"]) & pbp["posteam"].notna()].copy()
    if pbp.empty:
        return pd.DataFrame()

    # Seconds per play: time between consecutive snaps by the same offense on the same drive,
    # neutral situations only (score within 7, quarters 1-3) so game script doesn't distort it.
    pbp = pbp.sort_values(["game_id", "play_id"])
    nxt = pbp.groupby(["game_id"])[["game_seconds_remaining", "drive", "posteam"]].shift(-1)
    gap = pbp["game_seconds_remaining"] - nxt["game_seconds_remaining"]
    same = (nxt["drive"] == pbp["drive"]) & (nxt["posteam"] == pbp["posteam"])
    neutral = (pbp["score_differential"].abs() <= 7) & (pbp["qtr"] <= 3)
    pbp["snap_gap"] = gap.where(same & neutral & (gap > 0) & (gap < 60))

    lo, hi = config.GARBAGE_WP
    garbage = (pbp["qtr"] >= 4) & ((pbp["wp"] < lo) | (pbp["wp"] > hi))
    pbp["epa_ok"] = pbp["epa"].notna() & ~garbage
    pbp["epa_use"] = pbp["epa"].where(pbp["epa_ok"], 0.0)

    g = pbp.groupby(["game_id", "season", "week", "season_type", "posteam", "defteam", "home_team"], as_index=False).agg(
        plays=("play_type", "size"),
        epa_plays=("epa_ok", "sum"),
        epa_sum=("epa_use", "sum"),
        snap_gap_sum=("snap_gap", "sum"),
        snap_gap_n=("snap_gap", "count"),
    )
    g["is_home"] = (g["posteam"] == g["home_team"]).astype(int)
    return g.drop(columns=["home_team"])


def load_team_games(schedules, refresh_current=True):
    """One row per (game, offense). Includes points scored from the schedule."""
    frames = []
    for season in range(config.FIRST_SEASON, config.CURRENT_SEASON + 1):
        path = CACHE / f"team_games_{season}.parquet"
        stale = season == config.CURRENT_SEASON and refresh_current
        if stale or not path.exists():
            tg = _team_games_for_season(season)
            if tg.empty:
                continue
            tg.to_parquet(path)
        frames.append(pd.read_parquet(path))
    tg = pd.concat(frames, ignore_index=True)

    sched = schedules[["game_id", "home_team", "away_team", "home_score", "away_score", "gameday"]]
    tg = tg.merge(sched, on="game_id", how="inner")
    tg["points"] = np.where(tg["is_home"] == 1, tg["home_score"], tg["away_score"])
    tg = apply_neutral(tg, schedules)
    tg["epa_per_play"] = tg["epa_sum"] / tg["epa_plays"].clip(lower=1)
    return tg.drop(columns=["home_score", "away_score"])


def apply_neutral(team_games, schedules):
    """(Re)derive the home_field term from the schedule's current `neutral` column."""
    tg = team_games.drop(columns=["neutral", "home_field"], errors="ignore")
    tg = tg.merge(schedules[["game_id", "neutral"]], on="game_id", how="left")
    tg["home_field"] = np.where(tg["neutral"], 0, np.where(tg["is_home"] == 1, 1, -1))
    return tg


def with_neutral_rule(schedules, rule):
    s = schedules.copy()
    s["neutral"] = s["neutral_venue"] if rule == "venue" else s["neutral_nflverse"]
    return s

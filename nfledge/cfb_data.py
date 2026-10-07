"""College football data from CollegeFootballData (CFBD), shaped like the NFL tables.

Schedules  -> one row per game (game_id = CFBD id as str), same columns the NFL code uses.
Team games -> one row per (game, offense) from play-by-play, same columns as nfledge.data.

Call budget (free tier, 1,000/month): completed seasons are fetched once and cached forever.
For the current season: games + lines refresh at most every few hours; a week's plays are
re-fetched until a copy exists that was pulled 36h+ after that week's last kickoff.
"""
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config
from nfledge import cfbd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "cfbd_agg"
CACHE.mkdir(parents=True, exist_ok=True)
SEASONS = list(range(config.NCAAF_FIRST_SEASON, config.NCAAF_CURRENT_SEASON + 1))
GROUP = {"fbs": 0, "fcs": 1}  # everything else (D-II, D-III, unknown) -> 2
SCRIMMAGE = {
    "Rush", "Pass Reception", "Pass Incompletion", "Sack", "Rushing Touchdown", "Passing Touchdown",
    "Fumble Recovery (Own)", "Fumble Recovery (Opponent)", "Pass Interception Return", "Interception Return Touchdown",
    "Fumble Return Touchdown", "Interception", "Pass Interception", "Safety", "Pass", "Pass Completion",
}
GARBAGE_MARGIN = {1: 43, 2: 37, 3: 27, 4: 22}


def _cur_max_age(season, hours):
    return hours if season == config.NCAAF_CURRENT_SEASON else None


def group_of(cls):
    return GROUP.get(cls, 2)


# --- teams ------------------------------------------------------------------------------

def load_teams(season=None):
    season = season or config.NCAAF_CURRENT_SEASON
    data = cfbd.get("/teams", {"year": season}, f"teams_{season}", max_age_hours=_cur_max_age(season, 24 * 30))
    df = pd.DataFrame(data)
    df["logo"] = "https://a.espncdn.com/i/teamlogos/ncaa/500/" + df["id"].astype(str) + ".png"
    df["full"] = df["school"] + " " + df["mascot"].fillna("")
    return df.drop_duplicates("school").set_index("school")


# --- schedules ----------------------------------------------------------------------------

def _consensus(lines):
    """Median of providers' current (closing, once final) spread and total. Home-side betting notation."""
    sp = [l["spread"] for l in lines or [] if l.get("spread") is not None]
    tot = [l["overUnder"] for l in lines or [] if l.get("overUnder") is not None]
    half = lambda x: float(np.round(np.median(x) * 2) / 2) if x else np.nan  # noqa: E731
    return half(sp), half(tot)


def load_schedules(refresh=True):
    frames = []
    for season in SEASONS:
        max_age = _cur_max_age(season, 3 if refresh else 24 * 365)
        games = cfbd.get("/games", {"year": season, "seasonType": "both"}, f"games_{season}", max_age_hours=max_age)
        lines = cfbd.get("/lines", {"year": season}, f"lines_{season}",
                         max_age_hours=_cur_max_age(season, 20 if refresh else 24 * 365))
        lmap = {str(x["id"]): _consensus(x.get("lines")) for x in lines}
        g = pd.DataFrame(games)
        g = g[g["seasonType"].isin(["regular", "postseason"])]
        reg_max = int(g.loc[g["seasonType"] == "regular", "week"].max())
        out = pd.DataFrame({
            "game_id": g["id"].astype(str), "season": season,
            "week": np.where(g["seasonType"] == "postseason", reg_max + 1, g["week"]).astype(int),
            "game_type": np.where(g["seasonType"] == "postseason", "POST", "REG"),
            "kickoff_utc": pd.to_datetime(g["startDate"], utc=True),
            "home_team": g["homeTeam"], "away_team": g["awayTeam"],
            "home_id": g["homeId"], "away_id": g["awayId"],
            "home_class": g["homeClassification"], "away_class": g["awayClassification"],
            "home_score": pd.to_numeric(g["homePoints"], errors="coerce"),
            "away_score": pd.to_numeric(g["awayPoints"], errors="coerce"),
            "neutral": g["neutralSite"].fillna(False).astype(bool),
            "stadium": g["venue"], "completed": g["completed"].fillna(False).astype(bool),
            "conference_game": g.get("conferenceGame", False),
        })
        cl = out["game_id"].map(lmap)
        out["close_home_spread"] = cl.map(lambda x: x[0] if isinstance(x, tuple) else np.nan)
        out["close_total"] = cl.map(lambda x: x[1] if isinstance(x, tuple) else np.nan)
        frames.append(out)
    s = pd.concat(frames, ignore_index=True)
    s["final"] = s["completed"] & s["home_score"].notna() & s["away_score"].notna()
    # Lines CFBD lists for games not yet played are not closing lines; keep them only as market context.
    s["mkt_home_spread"], s["mkt_total"] = s["close_home_spread"], s["close_total"]
    s.loc[~s["final"], ["close_home_spread", "close_total"]] = np.nan
    s["neutral_nflverse"] = s["neutral"]  # same column name the site uses for "neutral rule at lock time"
    s["international"] = False
    s["pickable"] = (s["home_class"] == "fbs") & (s["away_class"] == "fbs")
    s["gameday"] = s["kickoff_utc"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    return s


def load_talent(seasons):
    """{season: {team: 247 team talent composite}}. One call per season, cached (current season: 30 days)."""
    out = {}
    for season in seasons:
        d = cfbd.get("/talent", {"year": season}, f"talent_{season}", max_age_hours=_cur_max_age(season, 24 * 30))
        out[season] = {r["team"]: float(r["talent"]) for r in d if r.get("talent") is not None}
    return out


def groups_by_season(sched):
    out = {}
    for season, d in sched.groupby("season"):
        m = {}
        for team, cls in list(zip(d["home_team"], d["home_class"])) + list(zip(d["away_team"], d["away_class"])):
            m[team] = min(m.get(team, 9), group_of(cls))
        out[int(season)] = m
    return out


# --- play-by-play -> team games -------------------------------------------------------------

def _plays_week(season, season_type, week, last_kickoff, now, refresh):
    name = f"plays_{season}_{season_type}_{week:02d}"
    settle = last_kickoff + timedelta(hours=36)

    def fresh(fetched_at):
        if not refresh or fetched_at >= settle:
            return True                                     # pulled after the week settled: final
        return (now - fetched_at) < timedelta(hours=12)      # still settling: at most twice a day
    cfbd.get("/plays", {"year": season, "week": week, "seasonType": season_type}, name, fresh=fresh)
    return name


def _aggregate(plays):
    df = pd.DataFrame(plays)
    if df.empty:
        return pd.DataFrame()
    df = df[df["playType"].isin(SCRIMMAGE) & df["offense"].notna()].copy()
    df["clock_s"] = df["clock"].map(lambda c: (c or {}).get("minutes", 0) * 60 + (c or {}).get("seconds", 0))
    df = df.sort_values(["gameId", "driveNumber", "playNumber"])
    nxt = df.groupby("gameId")[["clock_s", "driveId", "offense", "period"]].shift(-1)
    gap = df["clock_s"] - nxt["clock_s"]
    margin = (df["offenseScore"] - df["defenseScore"]).abs()
    same = (nxt["driveId"] == df["driveId"]) & (nxt["offense"] == df["offense"]) & (nxt["period"] == df["period"])
    neutral = (margin <= 7) & (df["period"] <= 3)
    df["snap_gap"] = gap.where(same & neutral & (gap > 0) & (gap < 60))
    limit = df["period"].map(GARBAGE_MARGIN).fillna(np.inf)
    garbage = margin > limit
    df["epa_ok"] = df["ppa"].notna() & ~garbage
    df["epa_use"] = pd.to_numeric(df["ppa"], errors="coerce").where(df["epa_ok"], 0.0)
    g = df.groupby(["gameId", "offense", "defense", "home"], as_index=False).agg(
        plays=("playType", "size"), epa_plays=("epa_ok", "sum"), epa_sum=("epa_use", "sum"),
        snap_gap_sum=("snap_gap", "sum"), snap_gap_n=("snap_gap", "count"))
    g["is_home"] = (g["offense"] == g["home"]).astype(int)
    return g.rename(columns={"gameId": "game_id", "offense": "posteam", "defense": "defteam"}).drop(columns="home")


def load_team_games(sched, refresh=True):
    now = datetime.now(timezone.utc)
    frames = []
    done = sched[sched["final"]]
    for (season, gtype), d in done.groupby(["season", "game_type"]):
        stype = "postseason" if gtype == "POST" else "regular"
        for week, last_ko in d.groupby("week")["kickoff_utc"].max().items():
            api_week = 1 if stype == "postseason" else int(week)
            name = _plays_week(season, stype, api_week, last_ko.to_pydatetime(), now, refresh)
            raw = cfbd._path(name)
            agg_path = CACHE / f"tg_{name}.parquet"
            if not agg_path.exists() or agg_path.stat().st_mtime < raw.stat().st_mtime:
                import gzip, json
                with gzip.open(raw, "rt") as f:
                    _aggregate(json.load(f)["data"]).to_parquet(agg_path)
            t = pd.read_parquet(agg_path)
            if len(t):
                t["season"], t["week"], t["season_type"] = season, int(week), gtype
                frames.append(t)
    tg = pd.concat(frames, ignore_index=True)
    tg["game_id"] = tg["game_id"].astype(str)
    sch = sched[["game_id", "home_score", "away_score", "neutral"]]
    tg = tg.merge(sch, on="game_id", how="inner")
    tg["points"] = np.where(tg["is_home"] == 1, tg["home_score"], tg["away_score"])
    tg["home_field"] = np.where(tg["neutral"], 0, np.where(tg["is_home"] == 1, 1, -1))
    tg["epa_per_play"] = tg["epa_sum"] / tg["epa_plays"].clip(lower=1)
    tg = tg[tg["epa_plays"] >= 10]  # drop fragmentary play-by-play
    return tg.drop(columns=["home_score", "away_score"])


# --- name matching for market feeds -------------------------------------------------------------

def _norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch == " ").replace("  ", " ").strip()


def name_resolver(teams):
    """Odds API full names ('Ohio State Buckeyes') -> CFBD school ('Ohio State')."""
    teams = teams[teams["classification"] == "fbs"]
    by_full = {_norm(r.full): school for school, r in teams.iterrows()}
    # an alias pointing at a name CFBD doesn't use would silently drop games; ignore it instead
    aliases = {_norm(k): v for k, v in config.NCAAF_NAME_ALIASES.items() if v in teams.index}
    by_school = {_norm(school): school for school in teams.index}
    mascot = {school: _norm(str(r.mascot)) for school, r in teams.iterrows()}

    def resolve(name):
        n = _norm(name)
        if n in aliases:
            return aliases[n]
        if n in by_full:
            return by_full[n]
        words = n.split()
        for k in range(len(words) - 1, 0, -1):  # "School Mascot" where only the school spelling differs
            cand, rest = " ".join(words[:k]), " ".join(words[k:])
            if cand in by_school and rest == mascot.get(by_school[cand]):
                return by_school[cand]
        return None
    return resolve

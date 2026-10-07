"""Opponent-adjusted EPA and pace ratings.

Efficiency model (one row per team-game offense, weighted by plays x recency):

    EPA/play[A on offense vs B] = mu + off[A] + def[B] + hfa * home_field

  home_field is +1 for the home offense, -1 for the away offense, 0 at neutral sites,
  so the full home/away swing is 2*hfa. def[B] is EPA allowed above average (lower = better).

Pace model (same structure, target = offensive plays in the game):

    plays[A vs B] = mu_p + pace_off[A] + pace_def[B] + hfa_p * home_field

Both are weighted ridge regressions that shrink toward a *prior* rather than toward zero.
The prior for season S is the end-of-season S-1 fit with team terms multiplied by
PRIOR_REGRESSION. Early in the season the prior dominates; as current-season games
accumulate (recency-weighted, half-life HALF_LIFE_WEEKS) the data takes over.

Points: team points in a game are modelled as a + b * plays + c * plays * EPA/play, fit by
least squares on all games available before the prediction date.

Home field: EPA/play barely moves at home, but scoreboards do (~+2 pts/game since 2021).
So HFA in points is estimated directly from results: the recency-weighted average home
margin in non-neutral games before the prediction date (current season weight 1, each prior
season half the weight of the next). Schedules are balanced home/away, so team strength
cancels out of that average. The EPA hfa term is still fit so ratings aren't biased by
home-heavy or road-heavy early schedules.
"""
import json
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd

import config
from nfledge.teams import TEAMS

TEAM_LIST = sorted(TEAMS)
T = len(TEAM_LIST)
IDX = {t: i for i, t in enumerate(TEAM_LIST)}


def _design(df):
    n = len(df)
    X = np.zeros((n, 2 + 2 * T))
    X[:, 0] = 1.0
    X[np.arange(n), 1 + df["posteam"].map(IDX).to_numpy()] = 1.0
    X[np.arange(n), 1 + T + df["defteam"].map(IDX).to_numpy()] = 1.0
    X[:, -1] = df["home_field"].to_numpy()
    return X


def _ridge_to_prior(X, y, w, beta0, lam):
    """argmin sum w (y - Xb)^2 + sum lam_j (b_j - beta0_j)^2"""
    XtW = X.T * w
    A = XtW @ X + np.diag(lam)
    b = XtW @ y + lam * beta0
    return np.linalg.solve(A, b)


def _center(beta):
    """Make team terms sum to zero, folding the mean into the intercept (predictions unchanged)."""
    beta = beta.copy()
    mo, md = beta[1:1 + T].mean(), beta[1 + T:1 + 2 * T].mean()
    beta[1:1 + T] -= mo
    beta[1 + T:1 + 2 * T] -= md
    beta[0] += mo + md
    return beta


@dataclass
class Fit:
    eff: np.ndarray            # [mu, off*32, def*32, hfa]
    pace: np.ndarray           # [mu_p, pace_off*32, pace_def*32, hfa_p]
    pts: tuple = (0.0, 0.0, 0.0)  # (a, b, c): points = a + b*plays + c*plays*epa
    hfa_pts: float = 0.0       # home field advantage in points (full margin swing)
    sec_per_play: dict = field(default_factory=dict)
    games_played: dict = field(default_factory=dict)

    def regressed(self):
        e, p = self.eff.copy(), self.pace.copy()
        e[1:-1] *= config.PRIOR_REGRESSION
        p[1:-1] *= config.PRIOR_REGRESSION
        return e, p


def _fit(tg, prior: Fit | None, target_week):
    """Fit one season's worth of rows (tg) toward `prior`."""
    k = 2 + 2 * T
    if prior is None:
        eff0 = np.zeros(k)
        eff0[0] = np.average(tg["epa_per_play"], weights=tg["epa_plays"]) if len(tg) else 0.0
        pace0 = np.zeros(k)
        pace0[0] = tg["plays"].mean() if len(tg) else 62.0
    else:
        eff0, pace0 = prior.regressed()

    lam_e = np.full(k, config.PRIOR_PLAYS)
    lam_e[0] = lam_e[-1] = config.LEAGUE_PRIOR_PLAYS
    lam_p = np.full(k, config.PACE_PRIOR_GAMES)
    lam_p[0] = lam_p[-1] = config.LEAGUE_PRIOR_PLAYS / 62.0

    if len(tg) == 0:
        return eff0, pace0

    decay = 0.5 ** ((target_week - tg["week"].to_numpy()) / config.HALF_LIFE_WEEKS)
    X = _design(tg)
    eff = _ridge_to_prior(X, tg["epa_per_play"].to_numpy(), tg["epa_plays"].to_numpy() * decay, eff0, lam_e)
    pace = _ridge_to_prior(X, tg["plays"].to_numpy().astype(float), decay, pace0, lam_p)
    return _center(eff), _center(pace)


def _points_model(tg):
    d = tg.dropna(subset=["points"])
    X = np.column_stack([np.ones(len(d)), d["plays"], d["plays"] * d["epa_per_play"]])
    coef, *_ = np.linalg.lstsq(X, d["points"].to_numpy(), rcond=None)
    return tuple(float(c) for c in coef)


def _hfa_points(history, season):
    home = history[history["home_field"] == 1]
    opp = history[["game_id", "posteam", "points"]].rename(columns={"posteam": "defteam", "points": "opp_points"})
    g = home.merge(opp, on=["game_id", "defteam"])
    if g.empty:
        return 0.0
    w = 0.5 ** (season - g["season"])
    return float(np.average(g["points"] - g["opp_points"], weights=w))


def _sec_per_play(cur, prev):
    out = {}
    for t in TEAM_LIST:
        c, p = cur[cur["posteam"] == t], prev[prev["posteam"] == t]
        s = c["snap_gap_sum"].sum() + 0.25 * p["snap_gap_sum"].sum()
        n = c["snap_gap_n"].sum() + 0.25 * p["snap_gap_n"].sum()
        out[t] = s / n if n else np.nan
    return out


class RatingsEngine:
    """Produces point-in-time fits using only games before (season, week)."""

    def __init__(self, team_games):
        self.tg = team_games.dropna(subset=["points"]).copy()

    @lru_cache(maxsize=None)
    def end_of_season(self, season) -> Fit | None:
        if season < config.FIRST_SEASON:
            return None
        rows = self.tg[self.tg["season"] == season]
        if rows.empty:
            return None
        prior = self.end_of_season(season - 1)
        eff, pace = _fit(rows, prior, target_week=rows["week"].max() + 1)
        return Fit(eff=eff, pace=pace)

    def as_of(self, season, week) -> Fit:
        prior = self.end_of_season(season - 1)
        cur = self.tg[(self.tg["season"] == season) & (self.tg["week"] < week)]
        prev = self.tg[self.tg["season"] == season - 1]
        eff, pace = _fit(cur, prior, target_week=week)
        history = self.tg[(self.tg["season"] < season) | ((self.tg["season"] == season) & (self.tg["week"] < week))]
        gp = cur.groupby("posteam").size().to_dict()
        return Fit(eff=eff, pace=pace, pts=_points_model(history), hfa_pts=_hfa_points(history, season),
                   sec_per_play=_sec_per_play(cur, prev), games_played=gp)


def project(fit: Fit, home, away, neutral=False):
    """Projected score plus the components that produced it."""
    h, a = IDX[home], IDX[away]
    hf = 0 if neutral else 1
    e, p = fit.eff, fit.pace
    ca, cb, cc = fit.pts

    def side(off, de):
        epa = e[0] + e[1 + off] + e[1 + T + de]
        plays = p[0] + p[1 + off] + p[1 + T + de]
        return epa, plays, ca + cb * plays + cc * plays * epa

    h_epa, h_plays, h_pts = side(h, a)
    a_epa, a_plays, a_pts = side(a, h)
    hfa_pts = fit.hfa_pts * hf
    h_pts += hfa_pts / 2
    a_pts -= hfa_pts / 2

    return {
        "home_pts": h_pts, "away_pts": a_pts,
        "margin": h_pts - a_pts, "total": h_pts + a_pts,
        "model_home_spread": -(h_pts - a_pts),
        "home_epa": h_epa, "away_epa": a_epa,
        "home_plays": h_plays, "away_plays": a_plays,
        "hfa_pts": hfa_pts,
    }


def ratings_table(fit: Fit):
    """Team-level ratings with ranks, for display."""
    rows = []
    for t in TEAM_LIST:
        i = IDX[t]
        rows.append({
            "team": t,
            "off_epa": fit.eff[1 + i], "def_epa": fit.eff[1 + T + i],
            "net_epa": fit.eff[1 + i] - fit.eff[1 + T + i],
            "pace_off": fit.pace[0] + fit.pace[1 + i],
            "sec_per_play": fit.sec_per_play.get(t, np.nan),
            "games": int(fit.games_played.get(t, 0)),
        })
    df = pd.DataFrame(rows)
    df["off_rank"] = df["off_epa"].rank(ascending=False, method="min").astype(int)
    df["def_rank"] = df["def_epa"].rank(ascending=True, method="min").astype(int)
    df["net_rank"] = df["net_epa"].rank(ascending=False, method="min").astype(int)
    df["pace_rank"] = df["pace_off"].rank(ascending=False, method="min").astype(int)
    df["spp_rank"] = df["sec_per_play"].rank(ascending=True, method="min").astype("Int64")
    return df.set_index("team")


def league_terms(fit: Fit):
    return {
        "mu_epa": float(fit.eff[0]), "hfa_epa": float(fit.eff[-1]),
        "mu_plays": float(fit.pace[0]), "hfa_plays": float(fit.pace[-1]),
        "pts_a": fit.pts[0], "pts_b": fit.pts[1], "pts_c": fit.pts[2],
        "hfa_pts": fit.hfa_pts,
    }


def fit_to_dict(fit: Fit, meta=None):
    return {
        "teams": TEAM_LIST, "eff": [float(x) for x in fit.eff], "pace": [float(x) for x in fit.pace],
        "pts": list(fit.pts), "hfa_pts": fit.hfa_pts,
        "sec_per_play": {k: (None if np.isnan(v) else float(v)) for k, v in fit.sec_per_play.items()},
        "games_played": {k: int(v) for k, v in fit.games_played.items()},
        "params": model_params(), **(meta or {}),
    }


def fit_from_dict(d) -> Fit:
    assert d["teams"] == TEAM_LIST
    return Fit(eff=np.array(d["eff"]), pace=np.array(d["pace"]), pts=tuple(d["pts"]), hfa_pts=d["hfa_pts"],
               sec_per_play={k: (np.nan if v is None else v) for k, v in d["sec_per_play"].items()},
               games_played=d["games_played"])


def model_params():
    keys = ["HALF_LIFE_WEEKS", "PRIOR_REGRESSION", "PRIOR_PLAYS", "LEAGUE_PRIOR_PLAYS", "PACE_PRIOR_GAMES", "GARBAGE_WP"]
    return {k: getattr(config, k) for k in keys}


def model_version():
    import hashlib
    h = hashlib.sha1(json.dumps(model_params(), sort_keys=True).encode()).hexdigest()[:7]
    return f"epa-ridge-1.0+{h}"

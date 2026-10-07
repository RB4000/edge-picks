"""Opponent-adjusted EPA and pace ratings (sport-agnostic; NFL and NCAAF share this code).

Efficiency model (one row per team-game offense, weighted by plays x recency):

    EPA/play[A on offense vs B] = mu + group_off[g(A)] + off[A] + group_def[g(B)] + def[B] + h * home_field

  home_field is +1 for the home offense, -1 for the away offense, 0 at neutral sites.
  def[B] is EPA allowed above average (lower = better). Group terms only exist for college
  (FCS / lower-division opponents get their own baseline; FBS is the reference). NFL has none.

Pace model (same structure, target = offensive plays in the game).

Both are weighted ridge regressions that shrink toward a *prior* rather than toward zero.
The prior for season S is the end-of-season S-1 fit with team terms multiplied by
PRIOR_REGRESSION. Early in the season the prior dominates; as current-season games
accumulate (recency-weighted, half-life HALF_LIFE_WEEKS) the data takes over.

Points: team points = a + b * plays + c * plays * EPA/play, least squares on all prior games.

HOME FIELD ENTERS THE PROJECTION EXACTLY ONCE, as `hfa_pts` (half added to the home score,
half subtracted from the away score). The `h` terms in the EPA and pace regressions are
nuisance controls only: they keep ratings from being inflated by a home-heavy schedule and
are discarded at projection time (see `project`, which never reads them).
  - NFL ("balanced"): hfa_pts = recency-weighted mean home margin in non-neutral games. Every
    team plays ~half its games at home, so team strength cancels out of that average.
  - NCAAF ("regression"): schedules are not balanced (big programs host weaker teams), so a
    raw average would overstate HFA. Instead: ridge regression of FBS-vs-FBS game margins on
    team-season strengths plus a home indicator (zero at neutral sites); hfa_pts is that coefficient.
"""
import hashlib
import json
from dataclasses import dataclass, field, replace
from functools import lru_cache

import numpy as np
import pandas as pd

import config
from nfledge.teams import TEAMS

NFL_TEAMS = tuple(sorted(TEAMS))
TEAM_LIST = list(NFL_TEAMS)  # backwards compatibility
NFL_PARAM_KEYS = ["HALF_LIFE_WEEKS", "PRIOR_REGRESSION", "PRIOR_PLAYS", "LEAGUE_PRIOR_PLAYS", "PACE_PRIOR_GAMES",
                  "GARBAGE_WP", "TOTALS_CALIBRATION", "NEUTRAL_RULE"]


def nfl_params():
    p = {k: getattr(config, k) for k in NFL_PARAM_KEYS}
    p.update(HFA_METHOD="balanced", N_GROUPS=0, PLAYS_DEFAULT=62.0, FIRST_SEASON=config.FIRST_SEASON,
             VERSION="epa-ridge-1.1")
    return p


def ncaaf_params():
    return dict(config.NCAAF_MODEL)


# --- layout helpers ------------------------------------------------------------------

def _layout(T, G):
    """Index ranges in the coefficient vector: [mu, off*T, def*T, goff*G, gdef*G, h]."""
    return {"mu": 0, "off": 1, "def": 1 + T, "goff": 1 + 2 * T, "gdef": 1 + 2 * T + G, "h": 1 + 2 * T + 2 * G,
            "k": 2 + 2 * T + 2 * G}


@dataclass
class Fit:
    eff: np.ndarray                # coefficient vector, see _layout
    pace: np.ndarray
    teams: tuple = NFL_TEAMS
    groups: dict = field(default_factory=dict)   # team -> group index (0 = reference); missing -> 0
    n_groups: int = 0
    pts: tuple = (0.0, 0.0, 0.0)   # (a, b, c): points = a + b*plays + c*plays*epa
    hfa_pts: float = 0.0           # home field advantage in points (full margin swing)
    total_offset: float = 0.0      # subtracted from projected totals (totals calibration)
    sec_per_play: dict = field(default_factory=dict)
    games_played: dict = field(default_factory=dict)

    @property
    def idx(self):
        return {t: i for i, t in enumerate(self.teams)}

    @property
    def L(self):
        return _layout(len(self.teams), self.n_groups)

    def team_eff(self, vec, team, side):
        """Absolute team term (group baseline + team deviation) for side 'off' or 'def'."""
        L, i = self.L, self.idx[team]
        g = self.groups.get(team, 0)
        base = vec[L["g" + side] + g - 1] if g > 0 else 0.0
        return base + vec[L[side] + i]


def _design(df, teams, groups, G):
    T, n = len(teams), len(df)
    L = _layout(T, G)
    idx = {t: i for i, t in enumerate(teams)}
    X = np.zeros((n, L["k"]))
    X[:, 0] = 1.0
    X[np.arange(n), L["off"] + df["posteam"].map(idx).to_numpy()] = 1.0
    X[np.arange(n), L["def"] + df["defteam"].map(idx).to_numpy()] = 1.0
    if G:
        og = df["posteam"].map(lambda t: groups.get(t, 0)).to_numpy()
        dg = df["defteam"].map(lambda t: groups.get(t, 0)).to_numpy()
        for g in range(1, G + 1):
            X[og == g, L["goff"] + g - 1] = 1.0
            X[dg == g, L["gdef"] + g - 1] = 1.0
    X[:, L["h"]] = df["home_field"].to_numpy()
    return X


def _ridge_to_prior(X, y, w, beta0, lam):
    """argmin sum w (y - Xb)^2 + sum lam_j (b_j - beta0_j)^2"""
    XtW = X.T * w
    A = XtW @ X + np.diag(lam)
    b = XtW @ y + lam * beta0
    return np.linalg.solve(A, b)


def _center(beta, teams, groups, G):
    """Make team deviations sum to zero within each group, folding the mean into the group term
    (or the intercept for the reference group). Predictions are unchanged."""
    beta = beta.copy()
    L = _layout(len(teams), G)
    gi = np.array([groups.get(t, 0) for t in teams])
    for side in ("off", "def"):
        sl = slice(L[side], L[side] + len(teams))
        dev = beta[sl]
        for g in range(G + 1):
            m = gi == g
            if not m.any():
                continue
            mean = dev[m].mean()
            dev[m] -= mean
            if g == 0:
                beta[0] += mean
            else:
                beta[L["g" + side] + g - 1] += mean
        beta[sl] = dev
    return beta


def _prior_vectors(prior: Fit | None, teams, groups, G, P, tg):
    """Prior means for this season's coefficient vector, mapped from last season's fit."""
    L = _layout(len(teams), G)
    eff0, pace0 = np.zeros(L["k"]), np.zeros(L["k"])
    if prior is None:
        eff0[0] = np.average(tg["epa_per_play"], weights=tg["epa_plays"]) if len(tg) else 0.0
        pace0[0] = tg["plays"].mean() if len(tg) else P["PLAYS_DEFAULT"]
        return eff0, pace0
    reg = P["PRIOR_REGRESSION"]
    pL = prior.L
    for vec0, pvec in ((eff0, prior.eff), (pace0, prior.pace)):
        vec0[0] = pvec[0]
        vec0[L["h"]] = pvec[pL["h"]]
        for g in range(1, min(G, prior.n_groups) + 1):  # group baselines are structural: not regressed
            vec0[L["goff"] + g - 1] = pvec[pL["goff"] + g - 1]
            vec0[L["gdef"] + g - 1] = pvec[pL["gdef"] + g - 1]
        pidx = prior.idx
        for i, t in enumerate(teams):
            if t not in pidx:
                continue
            g_now = groups.get(t, 0)
            for side in ("off", "def"):
                abs_last = prior.team_eff(pvec, t, side)
                base_now = pvec[pL["g" + side] + g_now - 1] if (g_now > 0 and g_now <= prior.n_groups) else 0.0
                vec0[L[side] + i] = (abs_last - base_now) * reg
    return eff0, pace0


def _fit(tg, prior, target_week, teams, groups, P):
    G = P["N_GROUPS"]
    L = _layout(len(teams), G)
    eff0, pace0 = _prior_vectors(prior, teams, groups, G, P, tg)
    lam_e = np.full(L["k"], P["PRIOR_PLAYS"])
    lam_e[0] = lam_e[L["h"]] = P["LEAGUE_PRIOR_PLAYS"]
    lam_p = np.full(L["k"], P["PACE_PRIOR_GAMES"])
    lam_p[0] = lam_p[L["h"]] = P["LEAGUE_PRIOR_PLAYS"] / P["PLAYS_DEFAULT"]
    if G:
        lam_e[L["goff"]:L["h"]] = P["GROUP_PRIOR_PLAYS"]
        lam_p[L["goff"]:L["h"]] = P["GROUP_PRIOR_PLAYS"] / P["PLAYS_DEFAULT"]
    if len(tg) == 0:
        return eff0, pace0
    decay = 0.5 ** ((target_week - tg["week"].to_numpy()) / P["HALF_LIFE_WEEKS"])
    X = _design(tg, teams, groups, G)
    eff = _ridge_to_prior(X, tg["epa_per_play"].to_numpy(), tg["epa_plays"].to_numpy() * decay, eff0, lam_e)
    pace = _ridge_to_prior(X, tg["plays"].to_numpy().astype(float), decay, pace0, lam_p)
    return _center(eff, teams, groups, G), _center(pace, teams, groups, G)


def _points_model(tg):
    d = tg.dropna(subset=["points"])
    X = np.column_stack([np.ones(len(d)), d["plays"], d["plays"] * d["epa_per_play"]])
    coef, *_ = np.linalg.lstsq(X, d["points"].to_numpy(), rcond=None)
    return tuple(float(c) for c in coef)


def _home_games(history):
    home = history[history["is_home"] == 1]
    opp = history[["game_id", "posteam", "points"]].rename(columns={"posteam": "defteam", "points": "opp_points"})
    return home.merge(opp, on=["game_id", "defteam"])


def _hfa_balanced(history, season):
    g = _home_games(history)
    g = g[g["home_field"] == 1]
    if g.empty:
        return 0.0
    w = 0.5 ** (season - g["season"])
    return float(np.average(g["points"] - g["opp_points"], weights=w))


def _hfa_regression(history, season, groups_by_season, lam=0.01):
    """Margin = strength[home, season] - strength[away, season] + hfa * not_neutral, FBS vs FBS only.
    lam is near zero on purpose: shrinking team strengths leaves big-program-hosts-small-program
    mismatches unexplained and they leak into hfa (lam=2 gives ~4.6 pts; ~0 gives ~3.1)."""
    g = _home_games(history)
    ref = g.apply(lambda r: groups_by_season.get(r.season, {}).get(r.posteam, 0) == 0
                  and groups_by_season.get(r.season, {}).get(r.defteam, 0) == 0, axis=1) if len(g) else g
    g = g[ref] if len(g) else g
    if len(g) < 50:
        return 0.0
    keys = sorted(set(zip(g["season"], g["posteam"])) | set(zip(g["season"], g["defteam"])))
    ki = {k: i for i, k in enumerate(keys)}
    n, K = len(g), len(keys)
    X = np.zeros((n, K + 1))
    X[np.arange(n), [ki[k] for k in zip(g["season"], g["posteam"])]] = 1.0
    X[np.arange(n), [ki[k] for k in zip(g["season"], g["defteam"])]] = -1.0
    X[:, K] = (g["home_field"] == 1).astype(float).to_numpy()
    y = (g["points"] - g["opp_points"]).to_numpy(dtype=float)
    w = (0.5 ** (season - g["season"])).to_numpy()
    lamv = np.full(K + 1, lam)
    lamv[K] = 1e-6
    return float(_ridge_to_prior(X, y, w, np.zeros(K + 1), lamv)[K])


def _sec_per_play(cur, prev, teams):
    c = cur.groupby("posteam")[["snap_gap_sum", "snap_gap_n"]].sum()
    p = prev.groupby("posteam")[["snap_gap_sum", "snap_gap_n"]].sum()
    out = {}
    for t in teams:
        s = (c["snap_gap_sum"].get(t, 0.0)) + 0.25 * (p["snap_gap_sum"].get(t, 0.0))
        n = (c["snap_gap_n"].get(t, 0.0)) + 0.25 * (p["snap_gap_n"].get(t, 0.0))
        out[t] = s / n if n else np.nan
    return out


class RatingsEngine:
    """Produces point-in-time fits using only games before (season, week).

    groups: {season: {team: group}} for college (0 = FBS). None for the NFL.
    """

    def __init__(self, team_games, schedules=None, params=None, groups=None):
        self.P = params or nfl_params()
        self.tg = team_games.dropna(subset=["points"]).copy()
        self.sched = schedules
        self.groups = groups or {}
        self.calib_start = (self.P["FIRST_SEASON"], 4)

    def _teams(self, season, rows):
        if self.P["N_GROUPS"] == 0:
            return NFL_TEAMS
        names = set(self.groups.get(season, {})) | set(rows["posteam"]) | set(rows["defteam"])
        return tuple(sorted(names))

    def _groups(self, season):
        return self.groups.get(season, {})

    @lru_cache(maxsize=None)
    def end_of_season(self, season) -> Fit | None:
        if season < self.P["FIRST_SEASON"]:
            return None
        rows = self.tg[self.tg["season"] == season]
        if rows.empty:
            return None
        prior = self.end_of_season(season - 1)
        teams, groups = self._teams(season, rows), self._groups(season)
        eff, pace = _fit(rows, prior, rows["week"].max() + 1, teams, groups, self.P)
        return Fit(eff=eff, pace=pace, teams=teams, groups=groups, n_groups=self.P["N_GROUPS"])

    def as_of(self, season, week) -> Fit:
        fit = self._raw_as_of(season, week)
        if self.sched is None or not self.P["TOTALS_CALIBRATION"]:
            return fit
        return replace(fit, total_offset=self.total_offset(season, week))

    def total_offset(self, season, week):
        """Recency-weighted median of (model total - market total) over every game before (season, week).
        Each earlier season gets half the weight of the next, matching the HFA estimate."""
        s = self.sched
        past = s[s["final"] & s["close_total"].notna() & s.get("pickable", True)
                 & ((s["season"] < season) | ((s["season"] == season) & (s["week"] < week)))
                 & ((s["season"] > self.calib_start[0]) | (s["week"] >= self.calib_start[1]))]
        if past.empty:
            return 0.0
        diffs, weights = [], []
        for (ss, ww), _ in past.groupby(["season", "week"]):
            d = self._week_total_diffs(ss, ww)
            diffs.append(d)
            weights.append(np.full(len(d), 0.5 ** (season - ss)))
        return _weighted_median(np.concatenate(diffs), np.concatenate(weights))

    @lru_cache(maxsize=None)
    def _week_total_diffs(self, season, week):
        fit = self._raw_as_of(season, week)
        s = self.sched
        wk = s[(s["season"] == season) & (s["week"] == week) & s["final"] & s["close_total"].notna()
               & s.get("pickable", True)]
        return np.array([project(fit, g.home_team, g.away_team, g.neutral)["total"] - g.close_total
                         for g in wk.itertuples()])

    @lru_cache(maxsize=None)
    def _raw_as_of(self, season, week) -> Fit:
        prior = self.end_of_season(season - 1)
        cur = self.tg[(self.tg["season"] == season) & (self.tg["week"] < week)]
        prev = self.tg[self.tg["season"] == season - 1]
        teams, groups = self._teams(season, cur), self._groups(season)
        eff, pace = _fit(cur, prior, week, teams, groups, self.P)
        history = self.tg[(self.tg["season"] < season) | ((self.tg["season"] == season) & (self.tg["week"] < week))]
        gp = cur.groupby("posteam").size().to_dict()
        if self.P["HFA_METHOD"] == "regression":
            hfa = _hfa_regression(history, season, self.groups)
        else:
            hfa = _hfa_balanced(history, season)
        return Fit(eff=eff, pace=pace, teams=teams, groups=groups, n_groups=self.P["N_GROUPS"],
                   pts=_points_model(history), hfa_pts=hfa,
                   sec_per_play=_sec_per_play(cur, prev, teams), games_played=gp)


def _weighted_median(x, w):
    o = np.argsort(x)
    cw = np.cumsum(w[o])
    return float(x[o][np.searchsorted(cw, cw[-1] / 2)])


def project(fit: Fit, home, away, neutral=False):
    """Projected score plus the components that produced it. Home field is applied once, here."""
    hf = 0 if neutral else 1
    e, p = fit.eff, fit.pace
    ca, cb, cc = fit.pts

    def side(off, de):
        epa = e[0] + fit.team_eff(e, off, "off") + fit.team_eff(e, de, "def")
        plays = p[0] + fit.team_eff(p, off, "off") + fit.team_eff(p, de, "def")
        return epa, plays, ca + cb * plays + cc * plays * epa

    h_epa, h_plays, h_pts = side(home, away)
    a_epa, a_plays, a_pts = side(away, home)
    hfa_pts = fit.hfa_pts * hf
    h_pts += hfa_pts / 2 - fit.total_offset / 2
    a_pts -= hfa_pts / 2 + fit.total_offset / 2

    return {
        "home_pts": h_pts, "away_pts": a_pts,
        "margin": h_pts - a_pts, "total": h_pts + a_pts,
        "model_home_spread": -(h_pts - a_pts),
        "home_epa": h_epa, "away_epa": a_epa,
        "home_plays": h_plays, "away_plays": a_plays,
        "hfa_pts": hfa_pts,
    }


def ratings_table(fit: Fit, rank_pool=None):
    """Team-level ratings with ranks, for display. rank_pool limits who is ranked (e.g. FBS only)."""
    rows = []
    for t in fit.teams:
        off, de = fit.team_eff(fit.eff, t, "off"), fit.team_eff(fit.eff, t, "def")
        rows.append({
            "team": t, "off_epa": off, "def_epa": de, "net_epa": off - de,
            "pace_off": fit.pace[0] + fit.team_eff(fit.pace, t, "off"),
            "sec_per_play": fit.sec_per_play.get(t, np.nan),
            "games": int(fit.games_played.get(t, 0)),
        })
    df = pd.DataFrame(rows).set_index("team")
    pool = df if rank_pool is None else df[df.index.isin(rank_pool)]
    df["off_rank"] = pool["off_epa"].rank(ascending=False, method="min")
    df["def_rank"] = pool["def_epa"].rank(ascending=True, method="min")
    df["net_rank"] = pool["net_epa"].rank(ascending=False, method="min")
    df["pace_rank"] = pool["pace_off"].rank(ascending=False, method="min")
    df["spp_rank"] = pool["sec_per_play"].rank(ascending=True, method="min")
    for c in ("off_rank", "def_rank", "net_rank", "pace_rank", "spp_rank"):
        df[c] = df[c].astype("Int64")
    df.attrs["pool_size"] = len(pool)
    return df


def league_terms(fit: Fit):
    L = fit.L
    return {
        "mu_epa": float(fit.eff[0]), "mu_plays": float(fit.pace[0]),
        "nuisance_home_epa": float(fit.eff[L["h"]]),  # ratings control only; not used in projections
        "pts_a": fit.pts[0], "pts_b": fit.pts[1], "pts_c": fit.pts[2],
        "hfa_pts": fit.hfa_pts, "total_offset": fit.total_offset,
    }


def fit_to_dict(fit: Fit, meta=None, params=None):
    params = params or nfl_params()
    d = {
        "teams": list(fit.teams), "eff": [float(x) for x in fit.eff], "pace": [float(x) for x in fit.pace],
        "pts": list(fit.pts), "hfa_pts": fit.hfa_pts, "total_offset": fit.total_offset,
        "neutral_rule": params["NEUTRAL_RULE"],
        "sec_per_play": {k: (None if np.isnan(v) else float(v)) for k, v in fit.sec_per_play.items()},
        "games_played": {k: int(v) for k, v in fit.games_played.items()},
        "params": model_params(params), **(meta or {}),
    }
    if fit.n_groups:
        d["groups"] = {t: g for t, g in fit.groups.items() if g and t in set(fit.teams)}
        d["n_groups"] = fit.n_groups
    return d


def fit_from_dict(d) -> Fit:
    # Snapshots written before v1.1 have no total_offset: they reproduce exactly as locked.
    return Fit(eff=np.array(d["eff"]), pace=np.array(d["pace"]), teams=tuple(d["teams"]),
               groups=d.get("groups", {}), n_groups=d.get("n_groups", 0),
               pts=tuple(d["pts"]), hfa_pts=d["hfa_pts"], total_offset=d.get("total_offset", 0.0),
               sec_per_play={k: (np.nan if v is None else v) for k, v in d["sec_per_play"].items()},
               games_played=d["games_played"])


def model_params(params=None):
    params = params or nfl_params()
    if params.get("VERSION", "").startswith("epa-ridge-1.1") and params["N_GROUPS"] == 0:
        return {k: params[k] for k in NFL_PARAM_KEYS}  # NFL: keep the published v1.1 hash stable
    return {k: v for k, v in params.items() if k != "VERSION"}


def model_version(params=None):
    params = params or nfl_params()
    h = hashlib.sha1(json.dumps(model_params(params), sort_keys=True).encode()).hexdigest()[:7]
    return f"{params['VERSION']}+{h}"

"""Walk-forward backtest.

For every game in BACKTEST_SEASONS, ratings are refit using only games from earlier weeks
(and earlier seasons), the game is projected, and the pick is graded against the nflverse
*closing* spread/total. We have no archive of first-seen lines for past seasons, so the
backtest is graded against closing lines, which are the hardest number to beat. The live
ledger grades against first-seen lines.
"""
import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config
from nfledge import data, picks, ratings

ROOT = Path(__file__).resolve().parent.parent


def run(schedules, team_games, seasons=None, sport=None, params=None, groups=None):
    """sport=None -> NFL (with the configured neutral rule). For NCAAF pass sport, params and groups."""
    if sport is None or sport.key == "nfl":
        seasons = seasons or config.BACKTEST_SEASONS
        schedules = data.with_neutral_rule(schedules, config.NEUTRAL_RULE)
        team_games = data.apply_neutral(team_games, schedules)
        eng = ratings.RatingsEngine(team_games, schedules)
        games = schedules[schedules["season"].isin(seasons) & schedules["final"]]
    else:
        seasons = seasons or config.NCAAF_BACKTEST_SEASONS
        eng = sport.engine(team_games, schedules, groups, params)
        games = schedules[schedules["season"].isin(seasons) & schedules["final"] & schedules["pickable"]
                          & schedules["close_home_spread"].notna() & schedules["close_total"].notna()]
    rows = []
    for (season, week), wk in games.groupby(["season", "week"]):
        fit = eng.as_of(season, week)
        idx = fit.idx
        for _, g in wk.iterrows():
            if g.home_team not in idx or g.away_team not in idx:
                continue
            pr = ratings.project(fit, g.home_team, g.away_team, g.neutral)
            pk = picks.make_pick(g.home_team, g.away_team, pr["margin"], pr["total"],
                                 g.close_home_spread, g.close_total)
            row = {
                "game_id": g.game_id, "season": season, "week": week, "game_type": g.game_type,
                "home": g.home_team, "away": g.away_team,
                "model_margin": pr["margin"], "model_total": pr["total"],
                "market_home_spread": g.close_home_spread, "market_total": g.close_total,
                "home_score": g.home_score, "away_score": g.away_score, **pk,
            }
            row["spread_result"] = picks.grade_spread(pk.get("spread_pick"), g.home_team, g.close_home_spread,
                                                      g.home_score, g.away_score)
            row["total_result"] = picks.grade_total(pk.get("total_pick"), g.close_total, g.home_score, g.away_score)
            rows.append(row)
    return pd.DataFrame(rows)


def summarize(bt):
    out = {}
    for mkt in ("spread", "total"):
        res = {"All": picks.record(bt[f"{mkt}_result"])}
        for name, *_ in config.TIERS:
            res[name] = picks.record(bt.loc[bt[f"{mkt}_tier"] == name, f"{mkt}_result"])
        out[mkt] = res
    out["by_season"] = {
        s: {m: picks.record(d[f"{m}_result"]) for m in ("spread", "total")} for s, d in bt.groupby("season")
    }
    actual_margin = bt["home_score"] - bt["away_score"]
    actual_total = bt["home_score"] + bt["away_score"]
    mkt_margin = -bt["market_home_spread"]
    out["accuracy"] = {
        "model_margin_mae": float(np.mean(np.abs(actual_margin - bt["model_margin"]))),
        "market_margin_mae": float(np.mean(np.abs(actual_margin - mkt_margin))),
        "model_total_mae": float(np.mean(np.abs(actual_total - bt["model_total"]))),
        "market_total_mae": float(np.mean(np.abs(actual_total - bt["market_total"]))),
        "margin_slope": float(np.polyfit(bt["model_margin"], actual_margin, 1)[0]),
        "model_vs_market_corr": float(np.corrcoef(bt["model_margin"], mkt_margin)[0, 1]),
    }
    mm = -bt["market_home_spread"]
    edge = bt["spread_edge"]
    out["dispersion"] = {
        "model_margin_sd": float(bt["model_margin"].std()), "market_margin_sd": float(mm.std()),
        "underdog_share": float(((bt["spread_pick"] != bt["home"]) == (bt["market_home_spread"] < 0)).mean()),
        "edge_median": float(edge.median()), "edge_ge_3_5": float((edge >= 3.5).mean()),
        "edge_ge_8": float((edge >= 8).mean()),
    }
    out["n_games"] = len(bt)
    picked = bt[bt["total_pick"].isin(["OVER", "UNDER"])]
    out["over_share"] = float((picked["total_pick"] == "OVER").mean())
    out["model_minus_market_total_median"] = float((bt["model_total"] - bt["market_total"]).median())
    return out


@contextlib.contextmanager
def _override(**kw):
    old = {k: getattr(config, k) for k in kw}
    for k, v in kw.items():
        setattr(config, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(config, k, v)


SENSITIVITY = [
    {"HALF_LIFE_WEEKS": 4.0}, {"HALF_LIFE_WEEKS": 10.0},
    {"PRIOR_REGRESSION": 0.4}, {"PRIOR_REGRESSION": 0.8},
    {"PRIOR_PLAYS": 150.0}, {"PRIOR_PLAYS": 600.0},
]


def _fmt(r):
    if not r["n"]:
        return "—", "—", "—", "—"
    return (r["label"], f"{r['pct']:.1%}", f"{r['units']:+.1f}u", f"{r['p_value']:.2f}")


def _before_after(before, after, title, why, labels):
    b_lab, a_lab = labels
    L = [f"## {title}\n", why + "\n",
         f"| | {b_lab} | {a_lab} |", "|---|---|---|",
         f"| Over share of total picks | {before['over_share']:.1%} | {after['over_share']:.1%} |",
         f"| Median model − market total | {before['model_minus_market_total_median']:+.2f} | "
         f"{after['model_minus_market_total_median']:+.2f} |",
         f"| Model total MAE | {before['accuracy']['model_total_mae']:.2f} | {after['accuracy']['model_total_mae']:.2f} |",
         "", f"| Tier | {b_lab} totals | {b_lab} % | {b_lab} units | {a_lab} totals | {a_lab} % | {a_lab} units |",
         "|---|---|---|---|---|---|---|"]
    for k in after["total"]:
        bb, aa = before["total"][k], after["total"][k]
        L.append(f"| {k} | {bb['label']} | {bb['pct']:.1%} | {bb['units']:+.1f}u | {aa['label']} | {aa['pct']:.1%} | {aa['units']:+.1f}u |")
    L.append("")
    return L


NFL_CALIB_WHY = ("v1.0 projected the *mean* total. NFL totals skew right (mean ≈ 1 pt above median) and market totals "
                 "sit near the median, so v1.0 leaned over. v1.1 subtracts a walk-forward offset: the recency-weighted "
                 "median of (model total − market total) over all games before the prediction week. Spreads are unchanged.")
NCAAF_CALIB_WHY = ("Same median-gap calibration as the NFL model: projected totals are shifted by the recency-weighted "
                   "median of (model total − market total) over all FBS-vs-FBS games before the prediction week. "
                   "Shown with and without it. Spreads are unchanged.")


def section(title, summary, sens, before, seasons, scope, close_src, calib):
    a = summary["accuracy"]
    L = [f"# {title}\n",
         f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · seasons {seasons[0]}–{seasons[-1]} "
         f"({scope}) · {summary['n_games']} games_\n",
         "**How this was run:** walk-forward. Before each week, ratings were refit using only games played in "
         f"earlier weeks. Picks are graded against {close_src} **closing** lines at standard -110 pricing "
         "(break-even 52.4%). Model parameters were set before the backtest was run and were not tuned to it.\n"]
    for mkt, t in (("spread", "Against the spread"), ("total", "Totals (over/under)")):
        L += [f"## {t}\n", "| Edge tier | Record | Win % | Units (-110) | p-value vs 52.4% |", "|---|---|---|---|---|"]
        L += [f"| {k} | " + " | ".join(_fmt(r)) + " |" for k, r in summary[mkt].items()]
        L.append("")
    L += ["## By season\n", "| Season | ATS | ATS % | Totals | Totals % |", "|---|---|---|---|---|"]
    for ss, d in summary["by_season"].items():
        L.append(f"| {ss} | {d['spread']['label']} | {d['spread']['pct']:.1%} | {d['total']['label']} | {d['total']['pct']:.1%} |")
    L += ["", "## Accuracy vs the market\n", "| | Model | Closing line |", "|---|---|---|",
          f"| Mean abs. error, margin (pts) | {a['model_margin_mae']:.2f} | {a['market_margin_mae']:.2f} |",
          f"| Mean abs. error, total (pts) | {a['model_total_mae']:.2f} | {a['market_total_mae']:.2f} |",
          f"\nCalibration slope (actual margin on model margin): {a['margin_slope']:.2f} "
          f"(1.0 = perfectly scaled; >1 means the model is too conservative). "
          f"Correlation of model margin with closing-line margin: {a['model_vs_market_corr']:.2f}.\n"]
    if before is not None:
        L += _before_after(before, summary, *calib)
    L += ["## Parameter sensitivity (not used to choose parameters)\n",
          "Each row changes one parameter from the default and reruns the whole backtest. "
          "If results swing a lot between rows, any single row's record is mostly noise.\n",
          "| Variant | ATS | ATS % | ATS 3.5+ | Totals | Totals % | Totals 3.5+ |", "|---|---|---|---|---|---|---|"]
    for name, sm in sens:
        sp, to = sm["spread"], sm["total"]
        L.append(f"| {name} | {sp['All']['label']} | {sp['All']['pct']:.1%} | {sp['3.5+']['label']} "
                 f"| {to['All']['label']} | {to['All']['pct']:.1%} | {to['3.5+']['label']} |")
    L += ["", "## Reading this honestly\n"] + _honest_notes(summary)
    return "\n".join(L) + "\n"


def _honest_notes(summary):
    notes = []
    for mkt, label in (("spread", "ATS"), ("total", "Totals")):
        r = summary[mkt]["All"]
        lo, hi = r["ci_lo"], r["ci_hi"]
        verdict = ("is not statistically distinguishable from break-even" if r["p_value"] > 0.05
                   else "is above break-even at conventional significance, but expect regression toward 50% live")
        if r["pct"] < picks.BREAKEVEN_110:
            verdict = "is below the 52.4% break-even; following every pick would have lost money"
        notes.append(f"- **{label}:** {r['label']} ({r['pct']:.1%}, 95% CI {lo:.1%}–{hi:.1%}) {verdict}.")
    notes.append("- Tier records are small samples. A tier being profitable in a backtest is weak evidence on its own; "
                 "check whether the pattern holds across seasons and across the sensitivity variants above.")
    notes.append("- Closing lines are sharper than the first-seen lines the live site grades against, so live results "
                 "against opening/early numbers may be modestly better than this — or not. Closing line value (CLV) on "
                 "the record page is the faster, less noisy signal of whether the model finds real edges.")
    notes.append("- The model knows nothing about injuries, QB changes, weather, or rest beyond what is already in "
                 "the play-by-play. Large edges are often the market pricing exactly that information.")
    return notes


NCAAF_SENSITIVITY = [
    {"HALF_LIFE_WEEKS": 4.0}, {"HALF_LIFE_WEEKS": 10.0},
    {"PRIOR_REGRESSION": 0.35}, {"PRIOR_REGRESSION": 0.65},
    {"PRIOR_PLAYS": 150.0}, {"PRIOR_PLAYS": 600.0},
]


def _payload(summary, before, seasons):
    return {**summary, "v1_0": before, "by_season": {str(k): v for k, v in summary["by_season"].items()},
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "seasons": seasons}


def run_nfl(sensitivity=True):
    schedules = data.load_schedules(refresh=False)
    team_games = data.load_team_games(schedules, refresh_current=False)
    bt = run(schedules, team_games)
    summary = summarize(bt)
    with _override(TOTALS_CALIBRATION=False, NEUTRAL_RULE="nflverse"):
        before = summarize(run(schedules, team_games))
    sens = [("**default**", summary)]
    if sensitivity:
        for ov in SENSITIVITY:
            with _override(**ov):
                sens.append((", ".join(f"{k}={v:g}" for k, v in ov.items()), summarize(run(schedules, team_games))))
    bt.to_csv(ROOT / "data" / "backtest_games.csv", index=False)
    md = section("NFL backtest", summary, sens, before, config.BACKTEST_SEASONS, "regular season + playoffs",
                 "nflverse", ("Totals calibration: v1.0 → v1.1", NFL_CALIB_WHY, ("v1.0", "v1.1")))
    return md, _payload(summary, before, config.BACKTEST_SEASONS)


COMPRESSION_WHY = (
    "v1.0's college spreads were compressed: model margins had a standard deviation of about 5 points against the "
    "market's 13, so actual margins ran about 2.1x the model's (calibration slope 2.1) and most spread picks were "
    "big underdogs. Three causes, three fixes:\n\n"
    "1. **Evidence was discarded.** Recency weights (6-week half-life) multiplied each game's weight down while the pull "
    "toward the prior stayed fixed, so even at season's end a team's rating was roughly 60% this season and 40% prior. "
    "v1.1 normalizes the recency weights to average 1. They still favor recent games, but the season's total evidence counts in full.\n"
    "2. **The prior compounded.** Each season started from last season's final ratings x 0.5, and those ratings were "
    "themselves pulled toward a halved prior. v1.1 replaces the flat 50% with a preseason prior model: last season's rating "
    "(fit from that season alone) plus the 247 roster-talent composite. The weights are estimated each season from earlier "
    "seasons only.\n"
    "3. **The points mapping and residual scale.** Points are fit per team from that team's own EPA, which understates how "
    "EPA differences turn into margins (0.68 vs about 0.79 points per EPA). v1.1 adds walk-forward scale calibration: the model's "
    "team-strength margin is multiplied by the least-squares slope of actual margin (home field removed) on model margin over "
    "every earlier game, and totals get the same treatment before the median-gap totals calibration. The calibration uses actual "
    "results, not market lines.")


def _compression(before, after):
    b, a = before, after
    rows = [("Model margin SD (market SD)", lambda s: f"{s['dispersion']['model_margin_sd']:.1f} ({s['dispersion']['market_margin_sd']:.1f})"),
            ("Calibration slope (1.0 = right scale)", lambda s: f"{s['accuracy']['margin_slope']:.2f}"),
            ("Correlation with closing margin", lambda s: f"{s['accuracy']['model_vs_market_corr']:.3f}"),
            ("Margin MAE (closing line: {:.2f})".format(a['accuracy']['market_margin_mae']), lambda s: f"{s['accuracy']['model_margin_mae']:.2f}"),
            ("Spread picks on the underdog", lambda s: f"{s['dispersion']['underdog_share']:.0%}"),
            ("Median spread edge", lambda s: f"{s['dispersion']['edge_median']:.1f}"),
            ("Spread edges 3.5+", lambda s: f"{s['dispersion']['edge_ge_3_5']:.0%}"),
            ("Spread edges 8+", lambda s: f"{s['dispersion']['edge_ge_8']:.0%}"),
            ("Total picks on the over", lambda s: f"{s['over_share']:.1%}"),
            ("ATS, all picks", lambda s: f"{s['spread']['All']['label']} ({s['spread']['All']['pct']:.1%})"),
            ("ATS, 3.5+ tier", lambda s: f"{s['spread']['3.5+']['label']} ({s['spread']['3.5+']['pct']:.1%})"),
            ("Totals, all picks", lambda s: f"{s['total']['All']['label']} ({s['total']['All']['pct']:.1%})"),
            ("Totals, 3.5+ tier", lambda s: f"{s['total']['3.5+']['label']} ({s['total']['3.5+']['pct']:.1%})")]
    L = ["## Spread compression: v1.0 → v1.1\n", COMPRESSION_WHY + "\n", "| | v1.0 | v1.1 |", "|---|---|---|"]
    L += [f"| {lab} | {f(b)} | {f(a)} |" for lab, f in rows]
    L += ["", "Spread edges of 8+ points are still common in v1.1. The model's margins now have the right scale, "
          "but it explains less than the market does (margin MAE above the closing line's). When it disagrees with the market "
          "by a touchdown, the backtest says that's usually the model missing information, not an edge.\n"]
    return L


def run_ncaaf(sensitivity=True):
    from nfledge import cfb_data
    from nfledge.sports import BY_KEY
    sport = BY_KEY["ncaaf"]
    sched = cfb_data.load_schedules(refresh=False)
    tg = cfb_data.load_team_games(sched, refresh=False)
    groups = cfb_data.groups_by_season(sched)
    base = sport.params()
    bt = run(sched, tg, sport=sport, params=base, groups=groups)
    summary = summarize(bt)
    v10 = {**base, "DECAY_NORMALIZE": False, "SCALE_CALIBRATION": False, "PRIOR_MODEL": None}
    v1_0 = summarize(run(sched, tg, sport=sport, params=v10, groups=groups))
    before = summarize(run(sched, tg, sport=sport, params={**base, "TOTALS_CALIBRATION": False}, groups=groups))
    sens = [("**default**", summary)]
    if sensitivity:
        for ov in NCAAF_SENSITIVITY:
            sm = summarize(run(sched, tg, sport=sport, params={**base, **ov}, groups=groups))
            sens.append((", ".join(f"{k}={v:g}" for k, v in ov.items()), sm))
    bt.to_csv(ROOT / "data" / "backtest_games_ncaaf.csv", index=False)
    md = section("NCAAF backtest (model v1.1)", summary, sens, before, config.NCAAF_BACKTEST_SEASONS,
                 "FBS vs FBS, regular season + bowls, games with a market line", "CFBD consensus",
                 ("Totals calibration: off → on", NCAAF_CALIB_WHY, ("uncalibrated", "calibrated")))
    head, sep, rest = md.partition("## Against the spread")
    md = head + "\n".join(_compression(v1_0, summary)) + "\n" + sep + rest
    return md, {**_payload(summary, before, config.NCAAF_BACKTEST_SEASONS), "model_v1_0": v1_0}


def main(only=None, sensitivity=True):
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "results").mkdir(exist_ok=True)
    out = {}
    for key, fn in (("nfl", run_nfl), ("ncaaf", run_ncaaf)):
        if only and key != only:
            continue
        md, payload = fn(sensitivity)
        (ROOT / "results" / f"backtest_{key}.md").write_text(md)
        (ROOT / "results" / f"backtest_summary_{key}.json").write_text(json.dumps(payload, indent=1))
        out[key] = payload
    parts = [(ROOT / "results" / f"backtest_{k}.md") for k in ("nfl", "ncaaf")]
    (ROOT / "backtest_results.md").write_text("\n".join(p.read_text() for p in parts if p.exists()))
    return out


if __name__ == "__main__":
    import sys
    main(only=sys.argv[1] if len(sys.argv) > 1 else None)
    print((ROOT / "backtest_results.md").read_text())

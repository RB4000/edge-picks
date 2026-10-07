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


def run(schedules, team_games, seasons=None):
    seasons = seasons or config.BACKTEST_SEASONS
    eng = ratings.RatingsEngine(team_games)
    rows = []
    games = schedules[schedules["season"].isin(seasons) & schedules["final"]]
    for (season, week), wk in games.groupby(["season", "week"]):
        fit = eng.as_of(season, week)
        for _, g in wk.iterrows():
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
    out["n_games"] = len(bt)
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


def write_report(summary, sens, path):
    a = summary["accuracy"]
    L = []
    L.append("# Backtest results\n")
    L.append(f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
             f"seasons {config.BACKTEST_SEASONS[0]}–{config.BACKTEST_SEASONS[-1]} (regular season + playoffs) · "
             f"{summary['n_games']} games_\n")
    L.append("**How this was run:** walk-forward. Before each week, ratings were refit using only games played in "
             "earlier weeks. Picks are graded against nflverse **closing** lines at standard -110 pricing "
             "(break-even 52.4%). Model parameters were set before the backtest was run and were not tuned to it.\n")
    for mkt, title in (("spread", "Against the spread"), ("total", "Totals (over/under)")):
        L.append(f"## {title}\n")
        L.append("| Edge tier | Record | Win % | Units (-110) | p-value vs 52.4% |")
        L.append("|---|---|---|---|---|")
        for k, r in summary[mkt].items():
            L.append(f"| {k} | " + " | ".join(_fmt(r)) + " |")
        L.append("")
    L.append("## By season\n")
    L.append("| Season | ATS | ATS % | Totals | Totals % |")
    L.append("|---|---|---|---|---|")
    for s, d in summary["by_season"].items():
        L.append(f"| {s} | {d['spread']['label']} | {d['spread']['pct']:.1%} | {d['total']['label']} | {d['total']['pct']:.1%} |")
    L.append("")
    L.append("## Accuracy vs the market\n")
    L.append("| | Model | Closing line |")
    L.append("|---|---|---|")
    L.append(f"| Mean abs. error, margin (pts) | {a['model_margin_mae']:.2f} | {a['market_margin_mae']:.2f} |")
    L.append(f"| Mean abs. error, total (pts) | {a['model_total_mae']:.2f} | {a['market_total_mae']:.2f} |")
    L.append(f"\nCalibration slope (actual margin on model margin): {a['margin_slope']:.2f} "
             f"(1.0 = perfectly scaled; >1 means the model is too conservative). "
             f"Correlation of model margin with closing-line margin: {a['model_vs_market_corr']:.2f}.\n")
    L.append("## Parameter sensitivity (not used to choose parameters)\n")
    L.append("Each row changes one parameter from the default and reruns the whole backtest. "
             "If results swing a lot between rows, any single row's record is mostly noise.\n")
    L.append("| Variant | ATS | ATS % | ATS 3.5+ | Totals | Totals % | Totals 3.5+ |")
    L.append("|---|---|---|---|---|---|---|")
    for name, s in sens:
        sp, to = s["spread"], s["total"]
        L.append(f"| {name} | {sp['All']['label']} | {sp['All']['pct']:.1%} | {sp['3.5+']['label']} "
                 f"| {to['All']['label']} | {to['All']['pct']:.1%} | {to['3.5+']['label']} |")
    L.append("")
    L.append("## Reading this honestly\n")
    L.extend(_honest_notes(summary))
    Path(path).write_text("\n".join(L) + "\n")


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


def main(schedules=None, team_games=None, sensitivity=True):
    schedules = schedules if schedules is not None else data.load_schedules(refresh=False)
    team_games = team_games if team_games is not None else data.load_team_games(schedules, refresh_current=False)
    bt = run(schedules, team_games)
    summary = summarize(bt)
    sens = []
    if sensitivity:
        sens.append(("**default**", summary))
        for ov in SENSITIVITY:
            with _override(**ov):
                s = summarize(run(schedules, team_games))
            sens.append((", ".join(f"{k}={v:g}" for k, v in ov.items()), s))
    (ROOT / "data").mkdir(exist_ok=True)
    bt.to_csv(ROOT / "data" / "backtest_games.csv", index=False)
    write_report(summary, sens, ROOT / "backtest_results.md")
    (ROOT / "results").mkdir(exist_ok=True)
    payload = {**summary, "by_season": {str(k): v for k, v in summary["by_season"].items()},
               "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "seasons": config.BACKTEST_SEASONS}
    (ROOT / "results" / "backtest_summary.json").write_text(json.dumps(payload, indent=1))
    return summary


if __name__ == "__main__":
    main()
    print((ROOT / "backtest_results.md").read_text())

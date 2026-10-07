"""`python -m nfledge.pipeline update` — the one idempotent command.

1. refresh nflverse schedules + play-by-play
2. capture market lines for the current week (snapshot every run; freeze the first one seen)
3. once every earlier game is final and in the play-by-play, fit ratings and lock picks
   for any not-yet-started game that has a frozen line (once per game, never revised)
4. record closing lines for finished games
5. regrade everything from final scores and rebuild ./site
"""
import argparse
import fcntl
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config
from nfledge import data, ledger, lines, picks, ratings

ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_kickoffs(sched):
    s = sched.copy()
    t = s["gametime"].fillna("13:00")
    local = pd.to_datetime(s["gameday"] + " " + t).dt.tz_localize(ET)
    s["kickoff_utc"] = local.dt.tz_convert("UTC")
    return s


def current_week(sched, season):
    cur = sched[sched["season"] == season]
    pending = cur[~cur["final"]]
    return int(pending["week"].min()) if len(pending) else None


def ready_to_lock(sched, team_games, season, week):
    """All earlier games must be final AND present in play-by-play, or ratings would be stale."""
    prior = sched[(sched["season"] == season) & (sched["week"] < week)]
    if not prior["final"].all():
        return False, "earlier games not final yet"
    missing = set(prior["game_id"]) - set(team_games["game_id"])
    if missing:
        return False, f"play-by-play not yet published for {len(missing)} game(s): {sorted(missing)[:3]}"
    return True, ""


def capture_lines(sched, season, week, now):
    wk = sched[(sched["season"] == season) & (sched["week"] == week)]
    upcoming = wk[wk["kickoff_utc"] > now]
    if upcoming.empty:
        return
    got = lines.fetch(sched[sched["season"] == season], season, week, log=log)
    got = got[got["game_id"].isin(upcoming["game_id"])]
    run_at = utc_iso(now)
    seen = set(zip(*[ledger.read("line_snapshots")[c] for c in ("game_id", "fetched_at")])) or set()
    snaps = [{**r, "run_at": run_at} for r in got.to_dict("records") if (r["game_id"], r["fetched_at"]) not in seen]
    n_snap = ledger.append("line_snapshots", snaps, unique=False)
    n_frz = ledger.append("frozen_lines", [{**r, "frozen_at": run_at} for r in got.to_dict("records")])
    log(f"  lines: {n_snap} snapshots recorded, {n_frz} newly frozen")


def lock_picks(sched, team_games, season, week, now):
    ok, why = ready_to_lock(sched, team_games, season, week)
    if not ok:
        log(f"  picks: not locking yet — {why}")
        return
    snap = ledger.read_ratings(season, week)
    if snap is None:
        fit = ratings.RatingsEngine(team_games).as_of(season, week)
        thru = team_games[(team_games["season"] == season) & (team_games["week"] < week)]
        snap = ratings.fit_to_dict(fit, {
            "season": season, "week": week, "fit_at": utc_iso(now),
            "data_through": f"{season} week {week - 1}" if len(thru) else f"{season - 1} season (prior only)",
            "model_version": ratings.model_version(),
        })
        p = ledger.write_ratings_once(season, week, snap)
        log(f"  ratings: wrote {p.name}")
    fit = ratings.fit_from_dict(snap)

    frozen = ledger.read("frozen_lines").set_index("game_id")
    locked = set(ledger.read("picks")["game_id"])
    wk = sched[(sched["season"] == season) & (sched["week"] == week)]
    rows = []
    for _, g in wk.iterrows():
        if g.game_id in locked or g.game_id not in frozen.index or g.kickoff_utc <= now:
            continue
        fl = frozen.loc[g.game_id]
        pr = ratings.project(fit, g.home_team, g.away_team, g.neutral)
        pk = picks.make_pick(g.home_team, g.away_team, pr["margin"], pr["total"], fl.home_spread, fl.total)
        rows.append({
            "game_id": g.game_id, "season": season, "week": week, "kickoff_utc": utc_iso(g.kickoff_utc),
            "away": g.away_team, "home": g.home_team, "locked_at": utc_iso(now),
            "line_home_spread": fl.home_spread, "line_total": fl.total,
            "line_source": fl.source, "line_fetched_at": fl.fetched_at,
            "model_away_pts": round(pr["away_pts"], 2), "model_home_pts": round(pr["home_pts"], 2),
            "model_home_spread": round(pr["model_home_spread"], 2), "model_total": round(pr["total"], 2),
            **pk, "ratings_file": ledger.ratings_path(season, week).name, "model_version": snap["model_version"],
        })
    n = ledger.append("picks", rows)
    log(f"  picks: {n} newly locked for {season} week {week}")


def record_closing(sched, now):
    """Closing line = nflverse closing line once final; never overwritten afterwards."""
    pk = ledger.read("picks")
    have = set(ledger.read("closing_lines")["game_id"])
    done = sched[sched["final"] & sched["game_id"].isin(pk["game_id"]) & ~sched["game_id"].isin(have)]
    snaps = ledger.read("line_snapshots")
    rows = []
    for _, g in done.iterrows():
        if pd.notna(g.close_home_spread) and pd.notna(g.close_total):
            rows.append({"game_id": g.game_id, "close_home_spread": g.close_home_spread,
                         "close_total": g.close_total, "close_source": "nflverse closing line",
                         "recorded_at": utc_iso(now)})
        else:  # fall back to our last pre-kickoff snapshot
            s = snaps[(snaps["game_id"] == g.game_id) & (pd.to_datetime(snaps["fetched_at"]) < g.kickoff_utc)]
            if len(s):
                last = s.iloc[-1]
                rows.append({"game_id": g.game_id, "close_home_spread": last.home_spread,
                             "close_total": last.total, "close_source": f"last pre-kickoff snapshot ({last.source})",
                             "recorded_at": utc_iso(now)})
    n = ledger.append("closing_lines", rows)
    if n:
        log(f"  closing lines: {n} recorded")


def graded(sched):
    """Picks joined with results, grades and CLV. Derived on every run, never stored by hand."""
    pk = ledger.read("picks")
    if pk.empty:
        return pk
    res = sched[["game_id", "home_score", "away_score", "final"]]
    cl = ledger.read("closing_lines")
    g = pk.merge(res, on="game_id", how="left").merge(cl, on="game_id", how="left")

    def row(r):
        out = {"spread_result": None, "total_result": None, "spread_clv": np.nan, "total_clv": np.nan}
        if r.final:
            out["spread_result"] = picks.grade_spread(r.spread_pick, r.home, r.line_home_spread, r.home_score, r.away_score)
            out["total_result"] = picks.grade_total(r.total_pick, r.line_total, r.home_score, r.away_score)
        if pd.notna(r.get("close_home_spread")) and r.spread_pick not in (None, "PASS"):
            close_pick_line = r.close_home_spread if r.spread_pick == r.home else -r.close_home_spread
            out["spread_clv"] = r.spread_pick_line - close_pick_line
        if pd.notna(r.get("close_total")) and r.total_pick in ("OVER", "UNDER"):
            d = r.close_total - r.line_total
            out["total_clv"] = d if r.total_pick == "OVER" else -d
        return pd.Series(out)

    return pd.concat([g, g.apply(row, axis=1)], axis=1)


def update(build=True, refresh=True):
    lockfile = open(ROOT / "data" / ".update.lock", "w")
    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another update is running; exiting")
        return 0
    now = datetime.now(timezone.utc)
    season = config.CURRENT_SEASON
    log("refreshing nflverse schedules + play-by-play")
    sched = add_kickoffs(data.load_schedules(refresh=refresh))
    tg = data.load_team_games(sched, refresh_current=refresh)
    week = current_week(sched, season)
    if week is None:
        log(f"{season} season complete; regrading and rebuilding only")
    else:
        log(f"current week: {season} week {week}")
        capture_lines(sched, season, week, now)
        lock_picks(sched, tg, season, week, now)
    record_closing(sched, now)
    g = graded(sched)
    if len(g):
        g.to_csv(ROOT / "ledger" / "graded.csv", index=False)
    if build:
        from nfledge import site
        site.build(sched, g, week)
        log("site rebuilt -> ./site")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["update", "build", "backtest", "sheet"])
    ap.add_argument("--no-refresh", action="store_true", help="use cached nflverse data")
    a = ap.parse_args(argv)
    if a.cmd == "update":
        return update(refresh=not a.no_refresh)
    if a.cmd == "build":
        from nfledge import site
        sched = add_kickoffs(data.load_schedules(refresh=False))
        site.build(sched, graded(sched), current_week(sched, config.CURRENT_SEASON))
        return 0
    if a.cmd == "backtest":
        from nfledge import backtest
        backtest.main()
        print((ROOT / "backtest_results.md").read_text())
        return 0
    if a.cmd == "sheet":
        from nfledge import sheet
        sched = add_kickoffs(data.load_schedules(refresh=False))
        sheet.print_sheet(sched, current_week(sched, config.CURRENT_SEASON))
        return 0


if __name__ == "__main__":
    sys.exit(main())

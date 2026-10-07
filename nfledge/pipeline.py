"""`python -m nfledge.pipeline update` — the one idempotent command, for every sport.

Per sport:
1. refresh schedules + play-by-play (nflverse for NFL, CollegeFootballData for NCAAF)
2. snapshot market lines for the current week (and next week once its freeze window opens);
   freeze each game's line at the first capture inside its freeze window
3. once earlier games are final and in the play-by-play, fit ratings and lock picks for any
   not-yet-started game with a frozen line (once per game, never revised)
4. record closing lines for finished games
5. regrade everything (vs the frozen line AND vs the closing line)
Then rebuild ./site.
"""
import argparse
import fcntl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config
from nfledge import lines, picks, ratings
from nfledge.sports import BY_KEY, SPORTS

ROOT = Path(__file__).resolve().parent.parent


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_local(dt):
    return dt.astimezone(ZoneInfo(config.FREEZE_TZ)).strftime("%a %b %-d %-I:%M %p %Z")


# --- freeze window ----------------------------------------------------------------------

def freeze_open(sport, season, week, kickoff_utc):
    """UTC time this game's freeze window opens: FREEZE_HOUR Central on the most recent Tuesday at or
    before kickoff. None for weeks before the sport's freeze policy start (frozen at first capture)."""
    if (season, week) < sport.freeze_policy_from:
        return None
    tz = ZoneInfo(config.FREEZE_TZ)
    ko = kickoff_utc.tz_convert(tz)
    day = ko.date() - timedelta(days=(ko.weekday() - config.FREEZE_WEEKDAY) % 7)
    opens = datetime(day.year, day.month, day.day, config.FREEZE_HOUR, tzinfo=tz)
    if opens > ko:  # a Tuesday game before 7am: use the previous Tuesday
        opens -= timedelta(days=7)
    return opens.astimezone(timezone.utc)


def capture_lines(sport, sched, week, now):
    led = sport.ledger
    s = sched[(sched["season"] == sport.season) & sched["pickable"]]
    games = s[s["week"].isin([week, week + 1]) & (s["kickoff_utc"] > now)].copy()
    if games.empty:
        return
    opens = {gid: freeze_open(sport, sport.season, w, k)
             for gid, w, k in zip(games["game_id"], games["week"], games["kickoff_utc"])}
    is_open = {gid: (o is None or o <= now) for gid, o in opens.items()}
    # next week only once its window is open (e.g. Tuesday 7am even if Monday night isn't final)
    games = games[(games["week"] == week) | games["game_id"].map(is_open)]
    if games.empty:
        log(f"  {sport.key} lines: nothing in scope (next week's freeze window not open yet)")
        return
    for w in sorted(games["week"].unique()):
        o = [opens[g] for g in games.loc[games["week"] == w, "game_id"] if opens[g] is not None]
        if o:
            log(f"  {sport.key} week {w}: freeze window {'opened' if min(o) <= now else 'opens'} {fmt_local(min(o))}")
        else:
            log(f"  {sport.key} week {w}: launch week, lines freeze at first capture")
    frozen = set(led.read("frozen_lines")["game_id"])
    open_unfrozen = [opens[g] for g in games["game_id"]
                     if opens[g] is not None and opens[g] <= now and g not in frozen]
    got = lines.fetch(sport.feed(sched), sched[sched["season"] == sport.season], sport.season, week, log=log,
                      not_before=max(open_unfrozen) if open_unfrozen else None, want=set(games["game_id"]))
    got = got[got["game_id"].isin(games["game_id"])]
    run_at = utc_iso(now)
    snaps_df = led.read("line_snapshots")
    seen = set(zip(snaps_df["game_id"].astype(str), snaps_df["fetched_at"]))
    recs = got.to_dict("records")
    n_snap = led.append("line_snapshots", [{**r, "run_at": run_at} for r in recs
                                           if (str(r["game_id"]), r["fetched_at"]) not in seen], unique=False)
    freezable = [r for r in recs if opens.get(r["game_id"]) is None
                 or pd.Timestamp(r["fetched_at"]) >= pd.Timestamp(opens[r["game_id"]])]
    n_frz = led.append("frozen_lines", [{**r, "frozen_at": run_at} for r in freezable])
    log(f"  {sport.key} lines: {n_snap} snapshots recorded, {n_frz} newly frozen "
        f"({len(games)} upcoming games in scope, {len(got)} with a market line)")


# --- locking ----------------------------------------------------------------------------

def lock_picks(sport, sched, tg, groups, week, now):
    led = sport.ledger
    start = getattr(sport, "coverage_from", None)
    if start and (sport.season, week) < start and not led.preview:
        log(f"  {sport.key} picks: week {week} is before public coverage starts (week {start[1]}); not locking")
        return
    ok, why = sport.ready_to_lock(sched, tg, week, now)
    if not ok:
        log(f"  {sport.key} picks: not locking yet — {why}")
        return
    snap = led.read_ratings(sport.season, week)
    if snap is None:
        fit = sport.engine(tg, sched, groups).as_of(sport.season, week)
        thru = tg[(tg["season"] == sport.season) & (tg["week"] < week)]
        snap = ratings.fit_to_dict(fit, {
            "season": sport.season, "week": week, "fit_at": utc_iso(now),
            "data_through": f"{sport.season} week {week - 1}" if len(thru) else f"{sport.season - 1} season (prior only)",
            "model_version": ratings.model_version(sport.params()),
        }, sport.params())
        p = led.write_ratings_once(sport.season, week, snap)
        log(f"  {sport.key} ratings: wrote {p.name}")
    fit = ratings.fit_from_dict(snap)
    idx = fit.idx
    frozen = led.read("frozen_lines").set_index("game_id")
    locked = set(led.read("picks")["game_id"])
    wk = sched[(sched["season"] == sport.season) & (sched["week"] == week) & sched["pickable"]]
    rows, skipped = [], 0
    for _, g in wk.iterrows():
        if g.game_id in locked or g.game_id not in frozen.index or g.kickoff_utc <= now:
            continue
        if g.home_team not in idx or g.away_team not in idx:
            skipped += 1
            continue
        fl = frozen.loc[g.game_id]
        pr = ratings.project(fit, g.home_team, g.away_team, g.neutral)
        pk = picks.make_pick(g.home_team, g.away_team, pr["margin"], pr["total"], fl.home_spread, fl.total)
        rows.append({
            "game_id": g.game_id, "season": sport.season, "week": week, "kickoff_utc": utc_iso(g.kickoff_utc),
            "away": g.away_team, "home": g.home_team, "locked_at": utc_iso(now),
            "line_home_spread": fl.home_spread, "line_total": fl.total,
            "line_source": fl.source, "line_fetched_at": fl.fetched_at,
            "model_away_pts": round(pr["away_pts"], 2), "model_home_pts": round(pr["home_pts"], 2),
            "model_home_spread": round(pr["model_home_spread"], 2), "model_total": round(pr["total"], 2),
            **pk, "ratings_file": led.ratings_path(sport.season, week).name, "model_version": snap["model_version"],
        })
    n = led.append("picks", rows)
    log(f"  {sport.key} picks: {n} newly locked for {sport.season} week {week}"
        + (f" ({skipped} skipped: team missing from ratings)" if skipped else ""))


def record_closing(sport, sched, now):
    """Closing line = the sport's published closing line once final (nflverse / CFBD consensus);
    otherwise our last pre-kickoff snapshot. Written once, never overwritten."""
    led = sport.ledger
    pk = led.read("picks")
    have = set(led.read("closing_lines")["game_id"])
    done = sched[sched["final"] & sched["game_id"].isin(pk["game_id"]) & ~sched["game_id"].isin(have)]
    snaps = led.read("line_snapshots")
    source = {"nfl": "nflverse closing line", "ncaaf": "CFBD closing consensus"}[sport.key]
    rows = []
    for _, g in done.iterrows():
        if pd.notna(g.close_home_spread) and pd.notna(g.close_total):
            rows.append({"game_id": g.game_id, "close_home_spread": g.close_home_spread,
                         "close_total": g.close_total, "close_source": source, "recorded_at": utc_iso(now)})
        else:
            s = snaps[(snaps["game_id"] == g.game_id) & (pd.to_datetime(snaps["fetched_at"]) < g.kickoff_utc)]
            if len(s):
                last = s.iloc[-1]
                rows.append({"game_id": g.game_id, "close_home_spread": last.home_spread, "close_total": last.total,
                             "close_source": f"last pre-kickoff snapshot ({last.source})", "recorded_at": utc_iso(now)})
    n = led.append("closing_lines", rows)
    if n:
        log(f"  {sport.key} closing lines: {n} recorded")


def graded(sport, sched):
    """Picks joined with results: graded vs the frozen line AND vs the closing line, plus CLV."""
    led = sport.ledger
    pk = led.read("picks")
    if pk.empty:
        return pk
    res = sched[["game_id", "home_score", "away_score", "final"]]
    cl = led.read("closing_lines")
    g = pk.merge(res, on="game_id", how="left").merge(cl, on="game_id", how="left")

    def row(r):
        out = {"spread_result": None, "total_result": None, "spread_result_close": None, "total_result_close": None,
               "spread_clv": np.nan, "total_clv": np.nan}
        has_close = pd.notna(r.get("close_home_spread")) and pd.notna(r.get("close_total"))
        if r.final == True:  # noqa: E712 (NaN-safe)
            out["spread_result"] = picks.grade_spread(r.spread_pick, r.home, r.line_home_spread, r.home_score, r.away_score)
            out["total_result"] = picks.grade_total(r.total_pick, r.line_total, r.home_score, r.away_score)
            if has_close:
                out["spread_result_close"] = picks.grade_spread(r.spread_pick, r.home, r.close_home_spread,
                                                                r.home_score, r.away_score)
                out["total_result_close"] = picks.grade_total(r.total_pick, r.close_total, r.home_score, r.away_score)
        if has_close and r.spread_pick not in (None, "PASS"):
            close_pick_line = r.close_home_spread if r.spread_pick == r.home else -r.close_home_spread
            out["spread_clv"] = r.spread_pick_line - close_pick_line
        if has_close and r.total_pick in ("OVER", "UNDER"):
            d = r.close_total - r.line_total
            out["total_clv"] = d if r.total_pick == "OVER" else -d
        return pd.Series(out)

    return pd.concat([g, g.apply(row, axis=1)], axis=1)


# --- orchestration ------------------------------------------------------------------------

def run_sport(sport, now, refresh=True):
    log(f"{sport.label}: refreshing schedules + play-by-play")
    sched, tg, groups = sport.load(refresh=refresh)
    week = sport.current_week(sched, now)
    if week is None:
        log(f"{sport.label}: {sport.season} season complete; regrading only")
    else:
        log(f"{sport.label}: current week {sport.season} week {week}")
        capture_lines(sport, sched, week, now)
        lock_picks(sport, sched, tg, groups, week, now)
    record_closing(sport, sched, now)
    g = graded(sport, sched)
    if len(g):
        g.to_csv(sport.ledger.root / "graded.csv", index=False)
    return {"sport": sport, "sched": sched, "graded": g, "week": week}


def update(build=True, refresh=True, only=None):
    (ROOT / "data").mkdir(exist_ok=True)
    lockfile = open(ROOT / "data" / ".update.lock", "w")
    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another update is running; exiting")
        return 0
    now = datetime.now(timezone.utc)
    results, failed = [], []
    for sport in SPORTS:
        if only and sport.key != only:
            continue
        try:
            results.append(run_sport(sport, now, refresh))
        except Exception as e:  # one sport's outage must not block the other
            log(f"{sport.label}: FAILED ({type(e).__name__}: {e})")
            failed.append(sport.key)
    from nfledge import cfbd
    log(cfbd.usage_line())
    u = lines.odds_usage()
    if u:
        log(f"Odds API: {u[0]} credits used this month, {u[1]} remaining")
    if build and results:
        from nfledge import site
        site.build_all(results if not only else load_results())
        log("site rebuilt -> ./site")
    return 1 if failed else 0


def load_results(refresh=False):
    now = datetime.now(timezone.utc)
    out = []
    for sport in SPORTS:
        sched, _, _ = sport.load(refresh=refresh)
        out.append({"sport": sport, "sched": sched, "graded": graded(sport, sched),
                    "week": sport.current_week(sched, now)})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["update", "build", "backtest", "sheet"])
    ap.add_argument("--sport", choices=list(BY_KEY), help="limit to one sport")
    ap.add_argument("--no-refresh", action="store_true", help="use cached data")
    a = ap.parse_args(argv)
    if a.cmd == "update":
        return update(refresh=not a.no_refresh, only=a.sport)
    if a.cmd == "build":
        from nfledge import site
        site.build_all(load_results())
        return 0
    if a.cmd == "backtest":
        from nfledge import backtest
        backtest.main(only=a.sport)
        print((ROOT / "backtest_results.md").read_text())
        return 0
    if a.cmd == "sheet":
        from nfledge import sheet
        for r in load_results():
            if not a.sport or r["sport"].key == a.sport:
                sheet.print_sheet(r["sport"], r["sched"], r["week"])
        return 0


if __name__ == "__main__":
    sys.exit(main())

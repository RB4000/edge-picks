"""Plain-text pick sheet for the terminal (and for copy/paste into posts)."""
import config
from nfledge.site import DISPLAY_TZ, fmt_line


def print_sheet(sport, sched, week):
    season = sport.season
    pk = sport.ledger.read("picks")
    pk = pk[(pk["season"] == season) & (pk["week"] == week)] if len(pk) else pk
    wk = sched[(sched["season"] == season) & (sched["week"] == week) & sched["pickable"]].sort_values(["kickoff_utc", "game_id"])
    by_id = pk.set_index("game_id") if len(pk) else None
    ab = sport.abbr
    w = 21 if sport.key == "ncaaf" else 11
    print(f"\n{config.BRAND_NAME} — {sport.label} {season} Week {week} pick sheet\n")
    hdr = (f"{'Kickoff (ET)':<18}{'Matchup':<{w}}{'Proj':>11}  {'Mkt sprd':>11}{'Model':>7}  {'Spread pick':<14}{'Edge':>5}"
           f"  {'Mkt tot':>7}{'Model':>6}  {'Total pick':<12}{'Edge':>5}")
    print(hdr)
    print("-" * len(hdr))
    n_none = 0
    for _, g in wk.iterrows():
        ko = g.kickoff_utc.astimezone(DISPLAY_TZ).strftime("%a %-m/%-d %-I:%M%p")
        m = f"{ab(g.away_team)}@{ab(g.home_team)}"
        if by_id is None or g.game_id not in by_id.index:
            n_none += 1
            continue
        p = by_id.loc[g.game_id]
        proj = f"{p.model_away_pts:.1f}-{p.model_home_pts:.1f}"
        sp = f"{ab(p.spread_pick)} {fmt_line(p.spread_pick_line)}" if p.spread_pick != "PASS" else "PASS"
        tp = f"{p.total_pick.title()} {p.line_total:g}" if p.total_pick != "PASS" else "PASS"
        star = lambda t: "*" if t == "3.5+" else " "  # noqa: E731
        print(f"{ko:<18}{m:<{w}}{proj:>11}  {ab(g.home_team) + ' ' + fmt_line(p.line_home_spread):>11}"
              f"{fmt_line(p.model_home_spread):>7}  {sp:<14}{p.spread_edge:>5.1f}{star(p.spread_tier)} {p.line_total:>6g}"
              f"{p.model_total:>6.1f}  {tp:<12}{p.total_edge:>5.1f}{star(p.total_tier)}")
    if len(pk):
        print(f"\n* = 3.5+ pt edge tier. {len(pk)} picks locked {pk['locked_at'].min()} UTC; lines frozen at first "
              f"capture ({pk['line_source'].iloc[0]}). Model {pk['model_version'].iloc[0]}.")
    if n_none:
        print(f"{n_none} game(s) without a locked pick (already kicked off, or no market line captured).")
    print()

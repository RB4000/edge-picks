"""Plain-text pick sheet for the terminal (and for copy/paste into posts)."""
import config
from nfledge import ledger
from nfledge.site import DISPLAY_TZ, fmt_line


def print_sheet(sched, week, season=None):
    season = season or config.CURRENT_SEASON
    pk = ledger.read("picks")
    pk = pk[(pk["season"] == season) & (pk["week"] == week)] if len(pk) else pk
    wk = sched[(sched["season"] == season) & (sched["week"] == week)].sort_values(["kickoff_utc", "game_id"])
    by_id = pk.set_index("game_id") if len(pk) else None
    print(f"\n{config.BRAND_NAME} — {season} Week {week} pick sheet\n")
    hdr = (f"{'Kickoff (ET)':<17}{'Matchup':<11}{'Proj':>11}  {'Mkt sprd':>9}{'Model':>7}  {'Spread pick':<12}{'Edge':>5}"
           f"  {'Mkt tot':>7}{'Model':>6}  {'Total pick':<12}{'Edge':>5}")
    print(hdr)
    print("-" * len(hdr))
    for _, g in wk.iterrows():
        ko = g.kickoff_utc.astimezone(DISPLAY_TZ).strftime("%a %-m/%-d %-I:%M%p")
        m = f"{g.away_team}@{g.home_team}"
        if by_id is None or g.game_id not in by_id.index:
            print(f"{ko:<17}{m:<11}{'no pick locked':>11}")
            continue
        p = by_id.loc[g.game_id]
        proj = f"{p.model_away_pts:.1f}-{p.model_home_pts:.1f}"
        sp = f"{p.spread_pick} {fmt_line(p.spread_pick_line)}" if p.spread_pick != "PASS" else "PASS"
        tp = f"{p.total_pick.title()} {p.line_total:g}" if p.total_pick != "PASS" else "PASS"
        star = lambda t: "*" if t == "3.5+" else " "
        print(f"{ko:<17}{m:<11}{proj:>11}  {g.home_team + ' ' + fmt_line(p.line_home_spread):>9}{fmt_line(p.model_home_spread):>7}  "
              f"{sp:<12}{p.spread_edge:>5.1f}{star(p.spread_tier)} {p.line_total:>6g}{p.model_total:>6.1f}  {tp:<12}{p.total_edge:>5.1f}{star(p.total_tier)}")
    if len(pk):
        print(f"\n* = 3.5+ pt edge tier. Lines frozen at first capture ({pk['line_source'].iloc[0]}); "
              f"locked {pk['locked_at'].min()} UTC. Model {pk['model_version'].iloc[0]}.")
    print()

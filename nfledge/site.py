"""Static site generator: ./site is rebuilt from scratch from the ledger + nflverse results."""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from nfledge import ledger, picks, ratings
from nfledge.teams import full_name, logo_url, short_name

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site"
DISPLAY_TZ = ZoneInfo(config.TIMEZONE_DISPLAY)


# --- formatting ---------------------------------------------------------------

def fmt_line(x, pk=True):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    x = float(x)
    if x == 0 and pk:
        return "PK"
    s = f"{abs(x):g}" if abs(x) % 0.5 == 0 else f"{abs(x):.1f}"
    return ("+" if x > 0 else "−") + s


def fmt_num(x, d=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{float(x):.{d}f}"


def fmt_signed(x, d=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{float(x):+.{d}f}".replace("-", "−")


def fmt_ts(iso):
    """UTC ISO -> 'Oct 7, 7:47 AM ET'"""
    if not isinstance(iso, str) or not iso:
        return "—"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(DISPLAY_TZ)
    return dt.strftime("%b %-d, %-I:%M %p ") + dt.strftime("%Z").replace("EDT", "ET").replace("EST", "ET")


def fmt_captured(iso):
    """UTC ISO -> 'captured Wed 10/7, 8:47 AM ET'"""
    if not isinstance(iso, str) or not iso:
        return "—"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(DISPLAY_TZ)
    return dt.strftime("captured %a %-m/%-d, %-I:%M %p ET")


def ordinal(n):
    n = int(n)
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def env():
    e = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))
    e.filters.update(captured=fmt_captured, line=fmt_line, num=fmt_num, signed=fmt_signed, ts=fmt_ts, ordinal=ordinal, pct=pct)
    e.globals.update(cfg=config, now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), tiers=[t[0] for t in config.TIERS],
                     logo=logo_url, full_name=full_name, short_name=short_name)
    return e


# --- view models --------------------------------------------------------------

def team_records(sched, season):
    """Record entering each week: {(team, week): 'W-L(-T)'}"""
    fin = sched[(sched["season"] == season) & sched["final"] & (sched["game_type"] == "REG")]
    rows = []
    for _, g in fin.iterrows():
        m = g.home_score - g.away_score
        rows.append((g.home_team, g.week, "W" if m > 0 else "L" if m < 0 else "T"))
        rows.append((g.away_team, g.week, "W" if m < 0 else "L" if m > 0 else "T"))
    df = pd.DataFrame(rows, columns=["team", "week", "r"])

    def rec(team, week):
        d = df[(df["team"] == team) & (df["week"] < week)]["r"]
        w, l, t = (d == "W").sum(), (d == "L").sum(), (d == "T").sum()
        return f"{w}-{l}" + (f"-{t}" if t else "")
    return rec


def tier_slug(t):
    return {"<2": "t1", "2-3.5": "t2", "3.5+": "t3"}.get(t, "t0")


def game_views(sched, graded, season, week, now):
    rec = team_records(sched, season)
    wk = sched[(sched["season"] == season) & (sched["week"] == week)].sort_values(["kickoff_utc", "game_id"])
    gi = graded.set_index("game_id") if len(graded) else pd.DataFrame()
    games = []
    for _, g in wk.iterrows():
        ko = g.kickoff_utc.astimezone(DISPLAY_TZ)
        v = {
            "id": g.game_id, "week": week, "season": season, "away": g.away_team, "home": g.home_team,
            "away_rec": rec(g.away_team, week), "home_rec": rec(g.home_team, week),
            "kickoff_iso": g.kickoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "day_key": ko.strftime("%Y-%m-%d"), "day_label": ko.strftime("%A, %B %-d"),
            "time_label": ko.strftime("%-I:%M %p ET"), "neutral": bool(g.neutral),
            "neutral_nflverse": bool(g.neutral_nflverse), "international": bool(g.international),
            "stadium": g.get("stadium"), "final": bool(g.final),
            "away_score": None if pd.isna(g.away_score) else int(g.away_score),
            "home_score": None if pd.isna(g.home_score) else int(g.home_score),
            "pick": None,
        }
        if g.game_id in gi.index:
            p = gi.loc[g.game_id].to_dict()
            p["spread_tier_slug"] = tier_slug(p.get("spread_tier"))
            p["total_tier_slug"] = tier_slug(p.get("total_tier"))
            v["pick"] = p
            v["status"] = "locked"
        elif g.kickoff_utc <= now:
            v["status"] = "nopick"
        else:
            v["status"] = "pending"
        games.append(v)
    days = []
    for g in games:
        if not days or days[-1]["key"] != g["day_key"]:
            days.append({"key": g["day_key"], "label": g["day_label"], "games": []})
        days[-1]["games"].append(g)
    return games, days


def explain(fit, g, snap):
    """Plain-language rating components for a game page, reproduced exactly as the model saw it at lock time."""
    tab = ratings.ratings_table(fit)
    lt = ratings.league_terms(fit)
    # Snapshots from before v1.1 used nflverse's location field for neutral sites.
    neutral_used = g["neutral"] if "neutral_rule" in snap else g["neutral_nflverse"]
    pr = ratings.project(fit, g["home"], g["away"], neutral_used)
    out = {"proj": pr, "league": lt, "teams": {}, "neutral_used": neutral_used}
    for side in ("away", "home"):
        t = g[side]
        r = tab.loc[t]
        out["teams"][side] = {
            "abbr": t, "off_epa": r.off_epa, "def_epa": r.def_epa, "off_rank": r.off_rank, "def_rank": r.def_rank,
            "net_rank": r.net_rank, "pace_off": r.pace_off, "pace_rank": r.pace_rank,
            "spp": r.sec_per_play, "spp_rank": None if pd.isna(r.spp_rank) else int(r.spp_rank),
            "games": int(r.games),
        }
    a, h = out["teams"]["away"], out["teams"]["home"]
    A, H = short_name(g["away"]), short_name(g["home"])
    off_word = lambda rk: "elite" if rk <= 5 else "above-average" if rk <= 12 else "middling" if rk <= 20 else "below-average" if rk <= 27 else "one of the league's worst"
    lines = [
        f"The {A} offense ranks {ordinal(a['off_rank'])} in opponent-adjusted EPA/play ({fmt_signed(a['off_epa'], 3)}), "
        f"facing a {H} defense that ranks {ordinal(h['def_rank'])} ({fmt_signed(h['def_epa'], 3)} EPA/play allowed vs. average). "
        f"Net, the model expects {fmt_signed(pr['away_epa'], 3)} EPA per {A} snap.",
        f"The {H} offense ranks {ordinal(h['off_rank'])} ({fmt_signed(h['off_epa'], 3)}), "
        f"against a {A} defense ranked {ordinal(a['def_rank'])} ({fmt_signed(a['def_epa'], 3)}). "
        f"Expected: {fmt_signed(pr['home_epa'], 3)} EPA per {H} snap.",
        f"Pace: the model projects {pr['away_plays']:.0f} offensive plays for the {A} and {pr['home_plays']:.0f} for the {H} "
        f"({pr['away_plays'] + pr['home_plays']:.0f} combined; league average is about {2 * lt['mu_plays']:.0f}). "
        + (f"In neutral situations the {A} snap the ball every {a['spp']:.1f}s ({ordinal(a['spp_rank'])} fastest) and "
           f"the {H} every {h['spp']:.1f}s ({ordinal(h['spp_rank'])})." if a["spp_rank"] and h["spp_rank"] else ""),
        (f"Home field is worth {lt['hfa_pts']:.1f} points to the {H}, estimated from actual home margins since "
         f"{config.FIRST_SEASON} (recent seasons weighted more)." if not neutral_used
         else "Neutral site: no home-field adjustment."),
        f"Efficiency × plays becomes points via a fit on every game since {config.FIRST_SEASON}: "
        f"points ≈ {lt['pts_a']:.1f} + {lt['pts_b']:.2f}×plays + {lt['pts_c']:.2f}×(plays × EPA/play).",
    ]
    if lt["total_offset"]:
        lines.append(f"Totals calibration: the projected total is lowered by {lt['total_offset']:.1f} points. NFL "
                     f"scoring skews high, so a mean projection runs above the typical (median) game the market prices. "
                     f"The offset is the median gap between model and market totals across all earlier games.")
    out["sentences"] = lines
    out["net_word"] = {side: off_word(out["teams"][side]["net_rank"]) for side in ("away", "home")}
    return out


def summary_records(graded, season):
    g = graded[(graded["season"] == season)] if len(graded) else graded
    out = {}
    for mkt in ("spread", "total"):
        col = f"{mkt}_result"
        res = {"All": picks.record(g[col]) if len(g) else picks.record([])}
        for name, *_ in config.TIERS:
            res[name] = picks.record(g.loc[g[f"{mkt}_tier"] == name, col]) if len(g) else picks.record([])
        clv = g[f"{mkt}_clv"].dropna() if len(g) else pd.Series(dtype=float)
        out[mkt] = {"tiers": res, "clv_avg": clv.mean() if len(clv) else None,
                    "clv_beat": (clv > 0).mean() if len(clv) else None, "clv_n": len(clv)}
    return out


# --- build ----------------------------------------------------------------------

def build(sched, graded, week):
    now = datetime.now(timezone.utc)
    season = config.CURRENT_SEASON
    e = env()
    tmp = OUT.with_name("site.tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "game").mkdir(parents=True)
    (tmp / "week").mkdir()
    shutil.copy(ROOT / "static" / "style.css", tmp / "style.css")
    shutil.copy(ROOT / "static" / "favicon.svg", tmp / "favicon.svg")

    graded = graded if len(graded) else pd.DataFrame(columns=ledger.FILES["picks"])
    weeks_with_picks = sorted(set(graded["week"].astype(int))) if len(graded) else []
    if week is None:
        week = max(weeks_with_picks) if weeks_with_picks else int(sched[sched.season == season]["week"].max())
    all_weeks = sorted(set(weeks_with_picks) | {week})

    def week_nav(cur):
        return [{"week": w, "href": f"week/{season}-{w:02d}.html", "current": w == cur} for w in all_weeks]

    for w in all_weeks:
        games, days = game_views(sched, graded, season, w, now)
        snap = ledger.read_ratings(season, w)
        ctx = dict(season=season, week=w, days=days, games=games, nav=week_nav(w), is_current=(w == week),
                   snap=snap, pending_reason=None)
        html_week = e.get_template("week.html").render(**ctx, root="../", page="week")
        (tmp / "week" / f"{season}-{w:02d}.html").write_text(html_week)
        if w == week:
            (tmp / "index.html").write_text(e.get_template("week.html").render(**ctx, root="", page="index"))
        fit = ratings.fit_from_dict(snap) if snap else None
        for g in games:
            ex = explain(fit, g, snap) if fit else None
            (tmp / "game" / f"{g['id']}.html").write_text(
                e.get_template("game.html").render(g=g, ex=ex, snap=snap, season=season, week=w, root="../", page="game"))

    # record page
    rec = summary_records(graded, season)
    rows = graded[graded["season"] == season].sort_values(["week", "kickoff_utc"], ascending=[False, True]) if len(graded) else graded
    rows = rows.to_dict("records")
    early = {}
    for r in rows:
        r["pre_policy"] = (int(r["season"]), int(r["week"])) < config.FREEZE_POLICY_FROM
        if r["pre_policy"]:
            early.setdefault(int(r["week"]), set()).add(fmt_captured(r["line_fetched_at"]).split(",")[0].replace("captured ", ""))
    pre_notes = [f"Week {w} lines were captured {', '.join(sorted(days))}, the day the site launched. By then the "
                 f"market had already moved off its Tuesday numbers, so these are not opening or Tuesday-morning lines."
                 for w, days in sorted(early.items())]
    (tmp / "record.html").write_text(e.get_template("record.html").render(
        rec=rec, rows=rows, season=season, root="", page="record", pre_notes=pre_notes,
        policy=config.FREEZE_POLICY_FROM))

    # method page with backtest
    bt_path = ROOT / "results" / "backtest_summary.json"
    bt = json.loads(bt_path.read_text()) if bt_path.exists() else None
    (tmp / "method.html").write_text(e.get_template("method.html").render(
        bt=bt, params=ratings.model_params(), version=ratings.model_version(), root="", page="method"))

    # raw ledger for anyone to audit
    (tmp / "ledger").mkdir()
    for f in ledger.LEDGER.glob("*.csv"):
        shutil.copy(f, tmp / "ledger" / f.name)
    if ledger.RATINGS.exists():
        shutil.copytree(ledger.RATINGS, tmp / "ledger" / "ratings")
    if (ROOT / "backtest_results.md").exists():
        shutil.copy(ROOT / "backtest_results.md", tmp / "ledger" / "backtest_results.md")
    (tmp / "_headers").write_text("/ledger/*\n  Content-Type: text/plain; charset=utf-8\n  Cache-Control: no-cache\n"
                                  "/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n")
    (tmp / "404.html").write_text(e.get_template("404.html").render(root="/", page="404"))

    if OUT.exists():
        shutil.rmtree(OUT)
    tmp.rename(OUT)

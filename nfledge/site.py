"""Static site generator: ./site is rebuilt from scratch from the ledgers + results on every run.

Layout:
  index.html                      hub: overall record strip + this week's strongest picks per sport
  {nfl,ncaaf}/index.html          current week
  {nfl,ncaaf}/week/S-WW.html      week archive
  {nfl,ncaaf}/game/<id>.html      per-game breakdown
  {nfl,ncaaf}/record.html         season record (vs frozen line and vs closing line)
  method.html                     methodology + backtests
  ledger/...                      raw public ledgers (NFL at ledger/, NCAAF at ledger/ncaaf/)
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from nfledge import ledger as ledger_mod
from nfledge import picks, ratings

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site"
DISPLAY_TZ = ZoneInfo(config.TIMEZONE_DISPLAY)


# --- formatting ---------------------------------------------------------------

def _missing(x):
    return x is None or (isinstance(x, float) and np.isnan(x)) or x is pd.NA


def fmt_line(x, pk=True):
    if _missing(x):
        return "—"
    x = float(x)
    if x == 0 and pk:
        return "PK"
    s = f"{abs(x):g}" if abs(x) % 0.5 == 0 else f"{abs(x):.1f}"
    return ("+" if x > 0 else "−") + s


def fmt_num(x, d=1):
    return "—" if _missing(x) else f"{float(x):.{d}f}"


def fmt_signed(x, d=1):
    return "—" if _missing(x) else f"{float(x):+.{d}f}".replace("-", "−")


def fmt_ts(iso):
    """UTC ISO -> 'Oct 7, 7:47 AM ET'"""
    if not isinstance(iso, str) or not iso:
        return "—"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(DISPLAY_TZ)
    return dt.strftime("%b %-d, %-I:%M %p ET")


def fmt_captured(iso):
    """UTC ISO -> 'captured Wed 10/7, 8:47 AM ET'"""
    if not isinstance(iso, str) or not iso:
        return "—"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(DISPLAY_TZ)
    return dt.strftime("captured %a %-m/%-d, %-I:%M %p ET")


def ordinal(n):
    if _missing(n):
        return "—"
    n = int(n)
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def pct(x):
    return "—" if _missing(x) else f"{x * 100:.1f}%"


def units(x):
    return "—" if _missing(x) else f"{x:+.1f}"


def env():
    e = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))
    e.filters.update(captured=fmt_captured, line=fmt_line, num=fmt_num, signed=fmt_signed, ts=fmt_ts,
                     ordinal=ordinal, pct=pct, units=units)
    e.globals.update(cfg=config, now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     tiers=[t[0] for t in config.TIERS])
    return e


# --- view models --------------------------------------------------------------

class SportView:
    """What templates need to know about a sport."""

    def __init__(self, sport):
        self.s = sport
        self.key, self.label = sport.key, sport.label
        self.article = "the " if sport.key == "nfl" else ""
        self.ledger_prefix = sport.ledger.public_prefix
        self.freeze_week = sport.freeze_policy_from[1]
        self.projections_only = sport.projections_only
        self.projections_note = config.NCAAF_PROJECTIONS_NOTE if self.projections_only else ""

    def name(self, t):
        return self.s.name(t)

    def short(self, t):
        return self.s.short(t)

    def abbr(self, t):
        return self.s.abbr(t)

    def logo(self, t):
        return self.s.logo(t)


def team_records(sched, season):
    """Record entering a week, any opponent: rec(team, week) -> 'W-L(-T)'."""
    fin = sched[(sched["season"] == season) & sched["final"] & (sched["game_type"] == "REG")]
    m = fin["home_score"] - fin["away_score"]
    df = pd.concat([
        pd.DataFrame({"team": fin["home_team"], "week": fin["week"], "r": np.sign(m)}),
        pd.DataFrame({"team": fin["away_team"], "week": fin["week"], "r": -np.sign(m)}),
    ])
    grouped = {t: d for t, d in df.groupby("team")}

    def rec(team, week):
        d = grouped.get(team)
        if d is None:
            return "0-0"
        r = d.loc[d["week"] < week, "r"]
        w, l, t = int((r > 0).sum()), int((r < 0).sum()), int((r == 0).sum())
        return f"{w}-{l}" + (f"-{t}" if t else "")
    return rec


def tier_slug(t):
    return {"<2": "t1", "2-3.5": "t2", "3.5+": "t3"}.get(t, "t0")


def game_views(sched, graded, season, week, now, pickable_only=True):
    rec = team_records(sched, season)
    wk = sched[(sched["season"] == season) & (sched["week"] == week)]
    if pickable_only:
        wk = wk[wk["pickable"]]
    wk = wk.sort_values(["kickoff_utc", "game_id"])
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
            "mkt_home_spread": g.get("mkt_home_spread"), "mkt_total": g.get("mkt_total"),
            "pick": None, "proj": None,
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


def add_projections(games, snap):
    """Projections-only sports: attach the model's projection from the week's ratings snapshot,
    plus the current market consensus for context (not a pick, not graded)."""
    fit = ratings.fit_from_dict(snap) if snap else None
    for g in games:
        g["status"] = "projection" if fit is not None else "pending"
        if fit is None or g["home"] not in fit.idx or g["away"] not in fit.idx:
            g["status"] = "pending"
            continue
        pr = ratings.project(fit, g["home"], g["away"], g["neutral"])
        g["proj"] = {"away_pts": pr["away_pts"], "home_pts": pr["home_pts"],
                     "home_spread": pr["model_home_spread"], "total": pr["total"],
                     "mkt_home_spread": g.get("mkt_home_spread"), "mkt_total": g.get("mkt_total")}
    return games


def explain(fit, g, snap, sv, rank_pool):
    """Plain-language rating components, reproduced exactly as the model saw them at lock time."""
    tab = ratings.ratings_table(fit, rank_pool)
    pool = tab.attrs["pool_size"]
    lt = ratings.league_terms(fit)
    # Snapshots from before v1.1 used nflverse's location field for neutral sites.
    neutral_used = g["neutral"] if "neutral_rule" in snap else g["neutral_nflverse"]
    pr = ratings.project(fit, g["home"], g["away"], neutral_used)
    out = {"proj": pr, "league": lt, "teams": {}, "neutral_used": neutral_used, "pool": pool}
    for side in ("away", "home"):
        t = g[side]
        r = tab.loc[t]
        d = {"abbr": t, "off_epa": r.off_epa, "def_epa": r.def_epa, "pace_off": r.pace_off,
             "spp": r.sec_per_play, "games": int(r.games)}
        for k in ("off_rank", "def_rank", "net_rank", "pace_rank", "spp_rank"):
            d[k] = None if pd.isna(r[k]) else int(r[k])
            d[k + "_q"] = None if d[k] is None else min(3, (d[k] - 1) * 4 // max(pool, 1))
        out["teams"][side] = d
    a, h = out["teams"]["away"], out["teams"]["home"]
    A, H = sv.article + sv.short(g["away"]), sv.article + sv.short(g["home"])
    A0, H0 = A[0].upper() + A[1:], H[0].upper() + H[1:]
    first = config.FIRST_SEASON if sv.key == "nfl" else config.NCAAF_FIRST_SEASON
    lines = [
        f"{A0} offense ranks {ordinal(a['off_rank'])} of {pool} in opponent-adjusted EPA/play "
        f"({fmt_signed(a['off_epa'], 3)}), facing {H} defense, which ranks {ordinal(h['def_rank'])} "
        f"({fmt_signed(h['def_epa'], 3)} EPA/play allowed vs. average). "
        f"Net, the model expects {fmt_signed(pr['away_epa'], 3)} EPA per {sv.short(g['away'])} snap.",
        f"{H0} offense ranks {ordinal(h['off_rank'])} ({fmt_signed(h['off_epa'], 3)}), "
        f"against {A} defense ranked {ordinal(a['def_rank'])} ({fmt_signed(a['def_epa'], 3)}). "
        f"Expected: {fmt_signed(pr['home_epa'], 3)} EPA per {sv.short(g['home'])} snap.",
        f"Pace: the model projects {pr['away_plays']:.0f} offensive plays for {A} and {pr['home_plays']:.0f} for {H} "
        f"({pr['away_plays'] + pr['home_plays']:.0f} combined; league average is about {2 * lt['mu_plays']:.0f}). "
        + (f"In neutral situations {A} snap the ball every {a['spp']:.1f}s ({ordinal(a['spp_rank'])} fastest) and "
           f"{H} every {h['spp']:.1f}s ({ordinal(h['spp_rank'])})." if a["spp_rank"] and h["spp_rank"] else ""),
        (f"Home field is worth {lt['hfa_pts']:.1f} points to {H}. This is the only place home field enters "
         f"the projection; it is estimated from game results since {first}."
         if not neutral_used else "Neutral site: no home-field advantage applied."),
        f"Efficiency × plays becomes points via a fit on every game since {first}: "
        f"points ≈ {lt['pts_a']:.1f} + {lt['pts_b']:.2f}×plays + {lt['pts_c']:.2f}×(plays × EPA/play).",
    ]
    if lt["total_offset"]:
        lines.append(f"Totals calibration: the projected total is lowered by {lt['total_offset']:.1f} points. Scoring "
                     f"skews high, so a mean projection runs above the typical (median) game the market prices. "
                     f"The offset is the median gap between model and market totals across all earlier games.")
    out["sentences"] = lines
    return out


def records(graded, season=None):
    """Records by market and tier, graded vs the frozen line and vs the closing line, plus CLV."""
    g = graded if season is None or not len(graded) else graded[graded["season"] == season]
    out = {}
    for mkt in ("spread", "total"):
        tiers = {}
        for name in ["All"] + [t[0] for t in config.TIERS]:
            sub = g if name == "All" or not len(g) else g[g[f"{mkt}_tier"] == name]
            close_col = f"{mkt}_result_close"
            tiers[name] = {
                "frozen": picks.record(sub[f"{mkt}_result"]) if len(sub) else picks.record([]),
                "close": picks.record(sub[close_col]) if len(sub) and close_col in sub else picks.record([]),
            }
        clv = g[f"{mkt}_clv"].dropna() if len(g) else pd.Series(dtype=float)
        out[mkt] = {"tiers": tiers, "clv_avg": clv.mean() if len(clv) else None,
                    "clv_beat": (clv > 0).mean() if len(clv) else None, "clv_n": len(clv)}
    return out


def experiment(graded, season):
    """The prospective 7+ point spread tracker: same picks, a separate tally."""
    ex = config.SPREAD_EXPERIMENT
    if not len(graded):
        return {"cfg": ex, "rows": [], "frozen": picks.record([]), "close": picks.record([]), "clv": None}
    g = graded[(graded["season"] == season) & (graded["week"] >= ex["from"][1]) & (graded["season"] >= ex["from"][0])
               & (graded["spread_pick"] != "PASS") & (graded["spread_edge"] >= ex["min_edge"])]
    g = g.sort_values(["week", "kickoff_utc"], ascending=[False, True])
    clv = g["spread_clv"].dropna()
    return {"cfg": ex, "rows": g.to_dict("records"), "frozen": picks.record(g["spread_result"]),
            "close": picks.record(g["spread_result_close"]), "clv": clv.mean() if len(clv) else None,
            "clv_n": len(clv)}


def pre_policy_notes(rows, sport):
    early = {}
    for r in rows:
        r["pre_policy"] = (int(r["season"]), int(r["week"])) < sport.freeze_policy_from
        if r["pre_policy"]:
            day = fmt_captured(r["line_fetched_at"]).split(",")[0].replace("captured ", "")
            early.setdefault(int(r["week"]), set()).add(day)
    return [sport.launch_note_tpl.format(week=w, days=", ".join(sorted(d)), label=sport.label)
            for w, d in sorted(early.items())]


# --- build ----------------------------------------------------------------------

def _empty_graded():
    cols = ledger_mod.FILES["picks"] + ["spread_result", "total_result", "spread_result_close", "total_result_close",
                                         "spread_clv", "total_clv", "final", "home_score", "away_score",
                                         "close_home_spread", "close_total"]
    return pd.DataFrame(columns=cols)


def build_sport(e, tmp, res, now):
    sport, sched, week = res["sport"], res["sched"], res["week"]
    sv = SportView(sport)
    season = sport.season
    graded = res["graded"] if len(res["graded"]) else _empty_graded()
    base = tmp / sport.key
    (base / "game").mkdir(parents=True)
    (base / "week").mkdir()

    weeks_with_picks = sorted(set(graded["week"].astype(int))) if len(graded) else []
    if sv.projections_only:  # weeks with a published ratings snapshot
        weeks_with_picks = sorted(int(p.stem.split("_w")[1]) for p in sport.ledger.ratings_dir.glob(f"{season}_w*.json"))
    if week is None:
        week = max(weeks_with_picks) if weeks_with_picks else int(sched[sched.season == season]["week"].max())
    all_weeks = sorted(set(weeks_with_picks) | {week})

    def nav(cur):
        return [{"week": w, "href": f"week/{season}-{w:02d}.html", "current": w == cur} for w in all_weeks]

    pool = sport.rank_pool(season)
    current = None
    for w in all_weeks:
        games, days = game_views(sched, graded, season, w, now)
        snap = sport.ledger.read_ratings(season, w)
        if sv.projections_only:
            add_projections(games, snap)
        ctx = dict(sp=sv, season=season, week=w, days=days, games=games, snap=snap,
                   n_locked=sum(g["status"] == "locked" for g in games))
        (base / "week" / f"{season}-{w:02d}.html").write_text(
            e.get_template("week.html").render(**ctx, nav=nav(w), root="../../", sroot="../", page="week"))
        if w == week:
            current = ctx
            (base / "index.html").write_text(
                e.get_template("week.html").render(**ctx, nav=nav(w), root="../", sroot="", page="index"))
        fit = ratings.fit_from_dict(snap) if snap else None
        for g in games:
            ok = fit is not None and g["home"] in fit.idx and g["away"] in fit.idx
            ex = explain(fit, g, snap, sv, pool) if ok else None
            (base / "game" / f"{g['id']}.html").write_text(e.get_template("game.html").render(
                sp=sv, g=g, ex=ex, snap=snap, season=season, week=w, root="../../", sroot="../", page="game"))

    rec = records(graded, season)
    if not sv.projections_only:  # projections-only sports have no record page
        rows = graded[graded["season"] == season].sort_values(["week", "kickoff_utc"], ascending=[False, True]) \
            if len(graded) else graded
        rows = rows.to_dict("records")
        notes = pre_policy_notes(rows, sport)
        (base / "record.html").write_text(e.get_template("record.html").render(
            sp=sv, rec=rec, rows=rows, season=season, root="../", sroot="", page="record", pre_notes=notes,
            exp=experiment(graded, season)))
    return {"sv": sv, "week": week, "current": current, "rec": rec, "graded": graded}


def build_all(results):
    now = datetime.now(timezone.utc)
    e = env()
    tmp = OUT.with_name("site.tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    shutil.copy(ROOT / "static" / "style.css", tmp / "style.css")
    shutil.copy(ROOT / "static" / "favicon.svg", tmp / "favicon.svg")

    built = [build_sport(e, tmp, r, now) for r in results]

    # hub
    parts = [b["graded"].assign(sport=b["sv"].key) for b in built if len(b["graded"])]
    overall = records(pd.concat(parts, ignore_index=True) if parts else _empty_graded())
    cards = []
    for b in built:
        cur = b["current"] or {"games": [], "week": b["week"], "n_locked": 0}
        locked = [g for g in cur["games"] if g["status"] == "locked"]
        top = sorted(locked, key=lambda g: -max(g["pick"].get("spread_edge") or 0, g["pick"].get("total_edge") or 0))[:5]
        cards.append({"sv": b["sv"], "week": cur["week"], "n_games": len(cur["games"]), "n_locked": cur["n_locked"],
                      "top": top, "rec": b["rec"]})
    (tmp / "index.html").write_text(e.get_template("home.html").render(overall=overall, cards=cards, root="", page="home"))

    # method page with backtests
    from nfledge.sports import BY_KEY
    bts = {}
    for key in BY_KEY:
        p = ROOT / "results" / f"backtest_summary_{key}.json"
        bts[key] = json.loads(p.read_text()) if p.exists() else None
    params = {k: (ratings.model_params(s.params()), ratings.model_version(s.params())) for k, s in BY_KEY.items()}
    (tmp / "method.html").write_text(e.get_template("method.html").render(bts=bts, params=params, root="", page="method"))

    # raw ledgers for anyone to audit (NFL paths unchanged since launch)
    for b in built:
        led = b["sv"].s.ledger
        dest = tmp / led.public_prefix
        dest.mkdir(parents=True, exist_ok=True)
        for f in led.public_files():
            shutil.copy(f, dest / f.name)
        if led.ratings_dir.exists():
            shutil.copytree(led.ratings_dir, dest / "ratings")
    (tmp / "ledger").mkdir(exist_ok=True)
    if (ROOT / "backtest_results.md").exists():
        shutil.copy(ROOT / "backtest_results.md", tmp / "ledger" / "backtest_results.md")
    (tmp / "_headers").write_text("/ledger/*\n  Content-Type: text/plain; charset=utf-8\n  Cache-Control: no-cache\n"
                                  "/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n")
    # pre-NCAAF NFL URLs keep working
    (tmp / "_redirects").write_text("/game/* /nfl/game/:splat 301\n/week/* /nfl/week/:splat 301\n"
                                    "/record.html /nfl/record.html 301\n/ncaaf/record.html /ncaaf/ 302\n"
                                    "/ncaaf/record /ncaaf/ 302\n")
    (tmp / "404.html").write_text(e.get_template("404.html").render(root="/", page="404"))

    if OUT.exists():
        shutil.rmtree(OUT)
    tmp.rename(OUT)

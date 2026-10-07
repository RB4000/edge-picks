# BRAND_NAME — NFL + college football spread & totals model

An opponent-adjusted EPA model that projects every NFL and FBS college game's score. NFL picks are published against a locked, timestamped line; college games get projections only. A static site shows each week's card, per-game breakdowns, and a season record graded automatically from final scores.

> Rename the brand in one place: `BRAND_NAME` in [`config.py`](config.py). The disclaimer text lives there too.

---

## The method, in plain English

**1. Efficiency, adjusted for opponent.** Every NFL snap gets an *expected points added* (EPA) value: how much the play changed the offense's expected points. We average it per team per game, dropping 4th-quarter snaps where the game is decided (win probability outside 10–90%). Raw EPA flatters teams that played bad defenses, so one weighted ridge regression solves for every offense and every defense at once. Each game's EPA/play is modeled as league average + offense rating + opponent defense rating + home/road term.

**2. Recent games count more. Last season is the starting point, not the answer.** Within a season, games are weighted by recency (6-week half-life). Each season starts from last season's final ratings, pulled 40% back toward average for roster turnover. That prior is worth about 5 games of data, so by midseason the current year dominates.

**3. Pace drives totals.** The same regression on offensive plays per game rates each team's pace and the pace its defense allows. We also track seconds per snap in neutral game states. Projected plays × projected EPA/play → projected points.

**4. Home field is measured, not assumed, and enters exactly once.** The regressions include a home/road term, but only as a control so a home-heavy early schedule doesn't inflate a team's ratings. It is thrown away at projection time. Home field enters the projected score once, as points: half added to the home side and half subtracted from the away side, so it moves the spread but not the total.
- *NFL:* the recency-weighted average home margin in non-neutral games since 2021 (about +2.1). NFL schedules are balanced, so team strength cancels out.
- *College:* big programs host smaller ones, so a raw average (+4.8) overstates it. A regression of FBS-vs-FBS margins on team-season strength plus a home indicator gives about +3.1.
- Neutral-site games (international, bowls, and London "home" games nflverse labels as Home) get zero.

**5. Totals are calibrated to how the market prices them.** NFL scoring skews high: a few shootouts pull the *average* game about a point above the *typical* (median) game. The model projects averages, while market totals sit near the median, so an uncalibrated model leans over. Each week's projected totals are shifted by the median gap between model and market totals across every earlier game. The model still says which games should be higher- or lower-scoring than the market expects. It no longer makes a blanket call on the whole league's scoring.

**6. Model line vs. market line.** Projected scores give a model spread and total. The difference from the market line is the **edge**, and the pick is whichever side the model prefers. Edges are tiered: **under 2**, **2–3.5**, and **3.5+** points.

### The rules (transparency is the product)

- Each game's line is **frozen at the first capture on or after 7:00 AM CT Tuesday** of game week. Picks are graded against it, never against a later number. (Week 5 of 2026, launch week, was frozen at first capture on Wed 10/7. The record page labels those rows.)
- A pick is generated and locked **once**, before kickoff, after every earlier game's play-by-play is in. It is never revised.
- The exact ratings used each week are published (`ledger/ratings/2026_w05.json`).
- Closing lines are recorded after kickoff. Every pick is graded twice, **vs. the frozen line** (the official record) and **vs. the closing line**, and **closing line value** (CLV) is reported. A model that wins at the frozen line but loses at the close is getting lucky with timing. One that beats the close is finding real edges.
- Every CSV in `ledger/` is append-only, committed to git, and published at `/ledger/` on the site.

### College football (NCAAF)

The same structure runs on CollegeFootballData play-by-play (PPA = their EPA) since 2021, with a few college-specific choices:
- garbage time is defined by score margin (>43/37/27/22 points in Q1–Q4)
- FCS and lower-division opponents get their own baseline, so cupcake games don't distort FBS ratings
- stronger regression to the mean between seasons (50%, vs. 40% in the NFL)
- home field is estimated separately (see above)

Picks are published only for FBS-vs-FBS games with a market line. CFBD's free tier is 1,000 calls/month; history is cached forever, and a normal day uses ~3 calls.

### What the model doesn't know

Injuries, QB changes, weather, travel, motivation — except as they've already shown up in play-by-play. When the model disagrees sharply with the market, the market is often pricing exactly that.

### Backtest (walk-forward, 2022–2025)

Before each week, ratings were refit using only earlier games. Picks were graded against **closing** lines at −110 (break-even 52.4%). Parameters were set before the backtest and **not tuned to it**. Full report with confidence intervals and a parameter-sensitivity table: [`backtest_results.md`](backtest_results.md).

Headline, NFL (v1.1): **ATS 547-563-29 (49.3%), totals 554-575-10 (49.1%)** against closing lines. NCAAF (v1.1, FBS vs FBS): **ATS 1519-1589-66 (48.9%), totals 1621-1508-45 (51.8%)**. Neither beats closing lines at −110 (break-even 52.4%), and the market-blend weight fit on results is zero for both models' spreads. So the site is framed as a transparent, timestamped, never-edited record of a public model, not as market-beating picks. College is projections only (no picks). One post-hoc slice, NFL spread disagreements of 7+ points, is tracked forward from Week 6 2026 as a separately tallied experiment (`SPREAD_EXPERIMENT` in `config.py`).

---

## Running it

```bash
make setup      # Python 3.11–3.13 virtualenv + deps (nfl_data_py's pandas<2 pin won't build on 3.13+, so this uses nflreadpy)
cp .env.example .env   # then add ODDS_API_KEY and CFBD_API_KEY
make update     # refresh data → capture/freeze lines → lock picks → regrade → rebuild ./site
make sheet      # print this week's pick sheet
make backtest   # rerun both walk-forward backtests → backtest_results.md (add SPORT=nfl|ncaaf for one)
make serve      # preview at http://localhost:8000
make deploy     # Cloudflare (Pages on Workers, via wrangler deploy; asks for a project name the first time)
```

`make update` is idempotent and safe on a schedule: it takes a file lock, appends only new ledger rows, and rebuilds the site atomically. See `ops/com.nfledge.update.plist` (launchd, recommended on macOS) and `ops/crontab.example` for a daily 7am CT run. `AUTO_DEPLOY=1` deploys after updating; `LEDGER_AUTOCOMMIT=1` commits ledger changes to git and pushes them to `origin` (outside timestamps). Replace `/path/to/nfl-edge` in those files with your checkout path; the plist header has a `sed` one-liner that does it.

### When picks lock

The "current week" is the earliest week with an unfinished game. Each run snapshots lines for that week, plus next week once its freeze window is open. A game's line freezes at the first capture on or after 7:00 AM CT on the Tuesday before the week's first kickoff (`FREEZE_*` in `config.py`). Earlier captures are logged in `line_snapshots.csv` but never frozen. Picks lock on the first run where (a) the line is frozen and (b) every earlier game is final **and** in nflverse play-by-play. With the daily 7am run, lines freeze Tuesday at 7am and picks usually lock at the same time, or Wednesday if the Monday-night play-by-play is late. Either way it's before Thursday kickoff.

### Market lines

1. **The Odds API** (`ODDS_API_KEY` in `.env`): median spread and total across US books, rounded to the half point. Responses are reused for 3 hours to conserve free-tier credits (~2 credits per call).
2. **ESPN public scoreboard JSON**: fallback when the API is down or out of credits.
3. **Last cached Odds API response**: last resort. It keeps its original fetch timestamp.

### Layout

```
config.py            brand, disclaimer, model parameters, edge tiers
nfledge/data.py      nflverse schedules + play-by-play → team-game table (cached parquet)
nfledge/ratings.py   ridge EPA + pace ratings, points model, HFA, projections
nfledge/lines.py     Odds API / ESPN / cache
nfledge/picks.py     edges, tiers, grading, records
nfledge/ledger.py    append-only ledger files
nfledge/pipeline.py  the update command
nfledge/backtest.py  walk-forward backtest
nfledge/site.py      static site generator (templates/, static/)
ledger/              the public record — commit this
```

---

*Model output for entertainment and information only. Not betting advice.*

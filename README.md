# BRAND_NAME — NFL spread & totals model

An opponent-adjusted EPA model that projects every NFL game's score, compares it to the market, and publishes the picks with a locked, timestamped line. A static site shows each week's card, per-game breakdowns, and a season record graded automatically from final scores.

> Rename the brand in one place: `BRAND_NAME` in [`config.py`](config.py). The disclaimer text lives there too.

---

## The method, in plain English

**1. Efficiency, adjusted for opponent.** Every NFL snap gets an *expected points added* (EPA) value: how much the play changed the offense's expected points. We average it per team per game, dropping 4th-quarter snaps where the game is decided (win probability outside 10–90%). Raw EPA flatters teams that played bad defenses, so one weighted ridge regression solves for every offense and every defense at once. Each game's EPA/play is modeled as league average + offense rating + opponent defense rating + home/road term.

**2. Recent games count more. Last season is the starting point, not the answer.** Within a season, games are weighted by recency (6-week half-life). Each season starts from last season's final ratings, pulled 40% back toward average for roster turnover. That prior is worth about 5 games of data, so by midseason the current year dominates.

**3. Pace drives totals.** The same regression on offensive plays per game rates each team's pace and the pace its defense allows. We also track seconds per snap in neutral game states. Projected plays × projected EPA/play → projected points.

**4. Home field is measured, not assumed.** It's the recency-weighted average home margin in non-neutral games since 2021 (currently about +2 points), and it updates as the league changes.

**5. Model line vs. market line.** Projected scores give a model spread and total. The difference from the market line is the **edge**, and the pick is whichever side the model prefers. Edges are tiered: **under 2**, **2–3.5**, and **3.5+** points.

### The rules (transparency is the product)

- The **first** market line captured for a game is frozen. Picks are graded against it, never against a later number.
- A pick is generated and locked **once**, before kickoff, after every earlier game's play-by-play is in. It is never revised.
- The exact ratings used each week are published (`ledger/ratings/2026_w05.json`).
- Closing lines are recorded after kickoff so **closing line value** (CLV) can be measured.
- Every CSV in `ledger/` is append-only, committed to git, and published at `/ledger/` on the site.

### What the model doesn't know

Injuries, QB changes, weather, travel, motivation — except as they've already shown up in play-by-play. When the model disagrees sharply with the market, the market is often pricing exactly that.

### Backtest (walk-forward, 2022–2025)

Before each week, ratings were refit using only earlier games. Picks were graded against **closing** lines at −110 (break-even 52.4%). Parameters were set before the backtest and **not tuned to it**. Full report with confidence intervals and a parameter-sensitivity table: [`backtest_results.md`](backtest_results.md).

Headline: **ATS 547-563-29 (49.3%), totals 542-587-10 (48.0%)** — no demonstrated edge against closing lines. The 3.5+ tiers ran 51.9% ATS and 53.4% on totals, both within noise (p = 0.58 and 0.37). This is published as-is. CLV on the live record page is the faster test of whether the model's early-week numbers beat where the market settles.

---

## Running it

```bash
make setup      # Python 3.11–3.13 virtualenv + deps (nfl_data_py's pandas<2 pin won't build on 3.13+, so this uses nflreadpy)
cp .env.example .env   # then add ODDS_API_KEY
make update     # refresh data → capture/freeze lines → lock picks → regrade → rebuild ./site
make sheet      # print this week's pick sheet
make backtest   # rerun the walk-forward backtest → backtest_results.md
make serve      # preview at http://localhost:8000
make deploy     # Cloudflare Pages (asks for a project name the first time)
```

`make update` is idempotent and safe on a schedule: it takes a file lock, appends only new ledger rows, and rebuilds the site atomically. See `ops/com.nfledge.update.plist` (launchd, recommended on macOS) and `ops/crontab.example` for a daily 7am CT run. `AUTO_DEPLOY=1` deploys after updating; `LEDGER_AUTOCOMMIT=1` commits ledger changes to git.

### When picks lock

The "current week" is the earliest week with an unfinished game. Lines are captured on every run for that week's games. Picks lock on the first run where (a) a line exists and (b) every earlier game is final **and** in nflverse play-by-play. With a daily 7am run, that's usually Tuesday, or Wednesday if the Monday-night play-by-play is late. Either way it's before Thursday kickoff.

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

# Weekly checklist

The daily 7am CT job does the work. This is the human part, about 15 minutes on Tuesday.

## Tuesday morning

- [ ] **Check the job ran:** `tail -40 data/logs/update.log`
  - Want to see: `freeze window opened Tue … 7:00 AM`, `N newly frozen`, `ratings: wrote 2026_wNN.json`, `N newly locked`.
  - Lines freeze at the first capture on or after **7:00 AM CT Tuesday**. If the Mac was asleep at 7, they freeze on the first run after it wakes. That's still correct, just later. Check the "Frozen line" timestamps on the record page.
  - If it says `not locking yet — play-by-play not yet published`, nflverse hasn't posted Monday night's game. Nothing to do: Wednesday's run will lock. (Or run `make update` later in the day.)
- [ ] **Check both sports ran:** look for `NFL: current week …` and `NCAAF: current week …` with no `FAILED`. One sport failing doesn't block the other.
- [ ] **Check API budgets** (last lines of the log): Odds API ~500 credits/month (two sports × daily run ≈ 120), CFBD 1,000 calls/month (≈ 90).
- [ ] **College name matching:** if the log says `unmatched names`, add the Odds API name → CFBD school to `NCAAF_NAME_ALIASES` in `config.py`. Unmatched games fall back to ESPN lines, so nothing is lost, but fix it anyway.
- [ ] **Read the sheet:** `make sheet` (or `make sheet SPORT=ncaaf`)
  - Every game should show a pick. "no pick locked" means no line was captured yet (rare: usually a flexed game or a late-posted line).
  - Sanity-check any 3.5+ edge. A huge edge on a team with a QB change usually means the market knows something the model can't see. **Don't edit anything.** The pick stands; that's the point. Just know about it before you post.
- [ ] **Look at last week:** open `site/record.html` (or `make serve`) and check:
  - Last week's ATS / totals result
  - Season record by tier
  - Average CLV (spread and total), and the **vs closing line** record next to the official vs-frozen record
- [ ] **Deploy** (if not on `AUTO_DEPLOY=1`): `make deploy`
- [ ] **Commit the ledger** (if not on `LEDGER_AUTOCOMMIT=1`): `git add ledger && git commit -m "week N picks"`. Pushing to a public repo gives you third-party timestamps.

## What to post

1. **The card (Tuesday/Wednesday):** screenshot the index page or paste from `make sheet`. Lead with the 3.5+ tier and link the site.
   > Week N is locked. Lines frozen [date/time], graded against those numbers no matter where they move. Full card + breakdowns: [link]
2. **Thursday:** the TNF game page (breakdown + pick), screenshot from `site/game/<id>.html`.
3. **Results (Tuesday, with the new card):** last week's record by tier plus CLV. Post losing weeks the same way you post winning ones. That's the brand.
   > Week N: ATS X-Y, totals X-Y. 3.5+ tier: X-Y. Avg CLV +Z pts. Season: … Every pick, line and timestamp: [link]/record.html
4. **Monthly:** link the methodology (README "The method, in plain English" is screenshot-ready) and `backtest_results.md`, including that the backtest did not beat closing lines.

## Don'ts

- Don't hand-edit anything in `ledger/`. If something is wrong, fix the code and let the next week be right. Note the issue publicly if it affected a published pick.
- Don't change model parameters mid-season. If you do between seasons, bump the version and say so. The model version is stamped on every pick.
- Don't run with an old cache and deploy without checking `make sheet` first.
- Don't call the output advice. The disclaimer stays in the footer.

## Season start (once a year)

- [ ] Bump `CURRENT_SEASON` in `config.py`
- [ ] `make backtest` to add the finished season to the report (`BACKTEST_SEASONS`)
- [ ] Odds API: check free-tier credits (500/month; a daily run uses ~60)

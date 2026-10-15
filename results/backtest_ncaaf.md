# NCAAF backtest (model v1.1, sides only)

_Generated 2026-10-15 14:46 UTC · seasons 2022–2025 (FBS vs FBS, regular season + bowls, games with a market line) · 3174 games_

**How this was run:** walk-forward. Before each week, ratings were refit using only games played in earlier weeks. Picks are graded against CFBD consensus **closing** lines at standard -110 pricing (break-even 52.4%). Model parameters were set before the backtest was run and were not tuned to it.

## Spread compression: v1.0 → v1.1

v1.0's college spreads were compressed: model margins had a standard deviation of about 5 points against the market's 13, so actual margins ran about 2.1x the model's (calibration slope 2.1) and most spread picks were big underdogs. Three causes, three fixes:

1. **Evidence was discarded.** Recency weights (6-week half-life) multiplied each game's weight down while the pull toward the prior stayed fixed, so even at season's end a team's rating was roughly 60% this season and 40% prior. v1.1 normalizes the recency weights to average 1. They still favor recent games, but the season's total evidence counts in full.
2. **The prior compounded.** Each season started from last season's final ratings x 0.5, and those ratings were themselves pulled toward a halved prior. v1.1 replaces the flat 50% with a preseason prior model: last season's rating (fit from that season alone) plus the 247 roster-talent composite. The weights are estimated each season from earlier seasons only.
3. **The points mapping and residual scale.** Points are fit per team from that team's own EPA, which understates how EPA differences turn into margins (0.68 vs about 0.79 points per EPA). v1.1 adds walk-forward scale calibration: the model's team-strength margin is multiplied by the least-squares slope of actual margin (home field removed) on model margin over every earlier game. The calibration uses actual results, not market lines.

| | v1.0 | v1.1 |
|---|---|---|
| Model margin SD (market SD) | 5.1 (13.2) | 11.6 (13.2) |
| Calibration slope (1.0 = right scale) | 2.11 | 0.96 |
| Correlation with closing margin | 0.823 | 0.859 |
| Margin MAE (closing line: 12.00) | 14.06 | 13.32 |
| Spread picks on the underdog | 90% | 60% |
| Median spread edge | 5.3 | 3.8 |
| Spread edges 3.5+ | 65% | 54% |
| Spread edges 8+ | 33% | 19% |
| ATS, all picks | 1557-1551-66 (50.1%) | 1519-1589-66 (48.9%) |
| ATS, 3.5+ tier | 1014-997-42 (50.4%) | 803-867-37 (48.1%) |

Spread edges of 8+ points are still common in v1.1. The model's margins now have the right scale, but it explains less than the market does (margin MAE above the closing line's). When it disagrees with the market by a touchdown, the backtest says that's usually the model missing information, not an edge.

## Against the spread

| Edge tier | Record | Win % | Units (-110) | p-value vs 52.4% |
|---|---|---|---|---|
| All | 1519-1589-66 | 48.9% | -228.9u | 1.00 |
| <2 | 426-440-17 | 49.2% | -58.0u | 0.97 |
| 2-3.5 | 290-282-12 | 50.7% | -20.2u | 0.79 |
| 3.5+ | 803-867-37 | 48.1% | -150.7u | 1.00 |

## By season

| Season | ATS | ATS % |
|---|---|---|
| 2022 | 377-383-16 | 49.6% |
| 2023 | 369-408-15 | 47.5% |
| 2024 | 385-396-17 | 49.3% |
| 2025 | 388-402-18 | 49.1% |

## Accuracy vs the market

| | Model | Closing line |
|---|---|---|
| Mean abs. error, margin (pts) | 13.32 | 12.00 |

Calibration slope (actual margin on model margin): 0.96 (1.0 = perfectly scaled; >1 means the model is too conservative). Correlation of model margin with closing-line margin: 0.86.

## Parameter sensitivity (not used to choose parameters)

Each row changes one parameter from the default and reruns the whole backtest. If results swing a lot between rows, any single row's record is mostly noise.

| Variant | ATS | ATS % | ATS 3.5+ |
|---|---|---|---|
| **default** | 1519-1589-66 | 48.9% | 803-867-37 |
| HALF_LIFE_WEEKS=4 | 1512-1596-66 | 48.6% | 825-891-38 |
| HALF_LIFE_WEEKS=10 | 1514-1594-66 | 48.7% | 799-862-36 |
| PRIOR_REGRESSION=0.35 | 1524-1584-66 | 49.0% | 813-876-37 |
| PRIOR_REGRESSION=0.65 | 1501-1607-66 | 48.3% | 790-859-35 |
| PRIOR_PLAYS=150 | 1518-1590-66 | 48.8% | 787-854-37 |
| PRIOR_PLAYS=600 | 1502-1606-66 | 48.3% | 852-906-39 |

## Reading this honestly

- **ATS:** 1519-1589-66 (48.9%, 95% CI 47.1%–50.6%) is below the 52.4% break-even; following every pick would have lost money.
- Tier records are small samples. A tier being profitable in a backtest is weak evidence on its own; check whether the pattern holds across seasons and across the sensitivity variants above.
- Closing lines are sharper than the first-seen lines the live site grades against, so live results against opening/early numbers may be modestly better than this — or not. Closing line value (CLV) on the record page is the faster, less noisy signal of whether the model finds real edges.
- The model knows nothing about injuries, QB changes, weather, or rest beyond what is already in the play-by-play. Large edges are often the market pricing exactly that information.

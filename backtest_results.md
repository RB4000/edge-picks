# Backtest results

_Generated 2026-10-07 12:45 UTC · seasons 2022–2025 (regular season + playoffs) · 1139 games_

**How this was run:** walk-forward. Before each week, ratings were refit using only games played in earlier weeks. Picks are graded against nflverse **closing** lines at standard -110 pricing (break-even 52.4%). Model parameters were set before the backtest was run and were not tuned to it.

## Against the spread

| Edge tier | Record | Win % | Units (-110) | p-value vs 52.4% |
|---|---|---|---|---|
| All | 547-563-29 | 49.3% | -72.3u | 0.98 |
| <2 | 227-259-14 | 46.7% | -57.9u | 0.99 |
| 2-3.5 | 139-136-7 | 50.5% | -10.6u | 0.73 |
| 3.5+ | 181-168-8 | 51.9% | -3.8u | 0.58 |

## Totals (over/under)

| Edge tier | Record | Win % | Units (-110) | p-value vs 52.4% |
|---|---|---|---|---|
| All | 542-587-10 | 48.0% | -103.7u | 1.00 |
| <2 | 276-323-5 | 46.1% | -79.3u | 1.00 |
| 2-3.5 | 126-142-3 | 47.0% | -30.2u | 0.96 |
| 3.5+ | 140-122-2 | 53.4% | +5.8u | 0.37 |

## By season

| Season | ATS | ATS % | Totals | Totals % |
|---|---|---|---|---|
| 2022 | 149-125-10 | 54.4% | 134-147-3 | 47.7% |
| 2023 | 132-139-14 | 48.7% | 125-156-4 | 44.5% |
| 2024 | 128-153-4 | 45.6% | 146-136-3 | 51.8% |
| 2025 | 138-146-1 | 48.6% | 137-148 | 48.1% |

## Accuracy vs the market

| | Model | Closing line |
|---|---|---|
| Mean abs. error, margin (pts) | 10.09 | 9.54 |
| Mean abs. error, total (pts) | 10.52 | 10.19 |

Calibration slope (actual margin on model margin): 1.43 (1.0 = perfectly scaled; >1 means the model is too conservative). Correlation of model margin with closing-line margin: 0.83.

## Parameter sensitivity (not used to choose parameters)

Each row changes one parameter from the default and reruns the whole backtest. If results swing a lot between rows, any single row's record is mostly noise.

| Variant | ATS | ATS % | ATS 3.5+ | Totals | Totals % | Totals 3.5+ |
|---|---|---|---|---|---|---|
| **default** | 547-563-29 | 49.3% | 181-168-8 | 542-587-10 | 48.0% | 140-122-2 |
| HALF_LIFE_WEEKS=4 | 547-563-29 | 49.3% | 216-190-9 | 553-576-10 | 49.0% | 142-126-3 |
| HALF_LIFE_WEEKS=10 | 537-573-29 | 48.4% | 164-159-6 | 542-587-10 | 48.0% | 131-123-2 |
| PRIOR_REGRESSION=0.4 | 553-557-29 | 49.8% | 195-183-10 | 546-583-10 | 48.4% | 148-129-3 |
| PRIOR_REGRESSION=0.8 | 546-564-29 | 49.2% | 172-159-6 | 541-588-10 | 47.9% | 136-124-2 |
| PRIOR_PLAYS=150 | 531-579-29 | 47.8% | 148-142-8 | 563-566-10 | 49.9% | 143-141-3 |
| PRIOR_PLAYS=600 | 557-553-29 | 50.2% | 228-216-10 | 550-579-10 | 48.7% | 151-141-3 |

## Reading this honestly

- **ATS:** 547-563-29 (49.3%, 95% CI 46.3%–52.2%) is below the 52.4% break-even; following every pick would have lost money.
- **Totals:** 542-587-10 (48.0%, 95% CI 45.1%–50.9%) is below the 52.4% break-even; following every pick would have lost money.
- Tier records are small samples. A tier being profitable in a backtest is weak evidence on its own; check whether the pattern holds across seasons and across the sensitivity variants above.
- Closing lines are sharper than the first-seen lines the live site grades against, so live results against opening/early numbers may be modestly better than this — or not. Closing line value (CLV) on the record page is the faster, less noisy signal of whether the model finds real edges.
- The model knows nothing about injuries, QB changes, weather, or rest beyond what is already in the play-by-play. Large edges are often the market pricing exactly that information.

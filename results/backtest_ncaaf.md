# NCAAF backtest

_Generated 2026-10-07 15:04 UTC · seasons 2022–2025 (FBS vs FBS, regular season + bowls, games with a market line) · 3174 games_

**How this was run:** walk-forward. Before each week, ratings were refit using only games played in earlier weeks. Picks are graded against CFBD consensus **closing** lines at standard -110 pricing (break-even 52.4%). Model parameters were set before the backtest was run and were not tuned to it.

## Against the spread

| Edge tier | Record | Win % | Units (-110) | p-value vs 52.4% |
|---|---|---|---|---|
| All | 1557-1551-66 | 50.1% | -149.1u | 0.99 |
| <2 | 318-336-12 | 48.6% | -51.6u | 0.97 |
| 2-3.5 | 225-218-12 | 50.8% | -14.8u | 0.75 |
| 3.5+ | 1014-997-42 | 50.4% | -82.7u | 0.96 |

## Totals (over/under)

| Edge tier | Record | Win % | Units (-110) | p-value vs 52.4% |
|---|---|---|---|---|
| All | 1627-1502-45 | 52.0% | -25.2u | 0.67 |
| <2 | 478-473-13 | 50.3% | -42.3u | 0.90 |
| 2-3.5 | 356-323-7 | 52.4% | +0.7u | 0.49 |
| 3.5+ | 793-706-25 | 52.9% | +16.4u | 0.34 |

## By season

| Season | ATS | ATS % | Totals | Totals % |
|---|---|---|---|---|
| 2022 | 386-374-16 | 50.8% | 373-390-13 | 48.9% |
| 2023 | 388-389-15 | 49.9% | 428-353-11 | 54.8% |
| 2024 | 392-389-17 | 50.2% | 416-367-15 | 53.1% |
| 2025 | 391-399-18 | 49.5% | 410-392-6 | 51.1% |

## Accuracy vs the market

| | Model | Closing line |
|---|---|---|
| Mean abs. error, margin (pts) | 14.06 | 12.00 |
| Mean abs. error, total (pts) | 12.98 | 12.53 |

Calibration slope (actual margin on model margin): 2.11 (1.0 = perfectly scaled; >1 means the model is too conservative). Correlation of model margin with closing-line margin: 0.82.

## Totals calibration: off → on

Same median-gap calibration as the NFL model: projected totals are shifted by the recency-weighted median of (model total − market total) over all FBS-vs-FBS games before the prediction week. Shown with and without it. Spreads are unchanged.

| | uncalibrated | calibrated |
|---|---|---|
| Over share of total picks | 62.3% | 55.1% |
| Median model − market total | +1.49 | +0.70 |
| Model total MAE | 13.03 | 12.98 |

| Tier | uncalibrated totals | uncalibrated % | uncalibrated units | calibrated totals | calibrated % | calibrated units |
|---|---|---|---|---|---|---|
| All | 1626-1503-45 | 52.0% | -27.3u | 1627-1502-45 | 52.0% | -25.2u |
| <2 | 467-447-14 | 51.1% | -24.7u | 478-473-13 | 50.3% | -42.3u |
| 2-3.5 | 313-319-6 | 49.5% | -37.9u | 356-323-7 | 52.4% | +0.7u |
| 3.5+ | 846-737-25 | 53.4% | +35.3u | 793-706-25 | 52.9% | +16.4u |

## Parameter sensitivity (not used to choose parameters)

Each row changes one parameter from the default and reruns the whole backtest. If results swing a lot between rows, any single row's record is mostly noise.

| Variant | ATS | ATS % | ATS 3.5+ | Totals | Totals % | Totals 3.5+ |
|---|---|---|---|---|---|---|
| **default** | 1557-1551-66 | 50.1% | 1014-997-42 | 1627-1502-45 | 52.0% | 793-706-25 |
| HALF_LIFE_WEEKS=4 | 1551-1557-66 | 49.9% | 1053-1017-46 | 1636-1493-45 | 52.3% | 831-728-26 |
| HALF_LIFE_WEEKS=10 | 1557-1551-66 | 50.1% | 985-978-41 | 1625-1504-45 | 51.9% | 787-684-24 |
| PRIOR_REGRESSION=0.35 | 1563-1545-66 | 50.3% | 1038-1011-44 | 1632-1497-45 | 52.2% | 811-712-24 |
| PRIOR_REGRESSION=0.65 | 1545-1563-66 | 49.7% | 997-968-43 | 1610-1519-45 | 51.5% | 776-694-27 |
| PRIOR_PLAYS=150 | 1538-1570-66 | 49.5% | 943-917-37 | 1630-1499-45 | 52.1% | 798-723-24 |
| PRIOR_PLAYS=600 | 1550-1558-66 | 49.9% | 1076-1074-49 | 1637-1492-45 | 52.3% | 858-757-27 |

## Reading this honestly

- **ATS:** 1557-1551-66 (50.1%, 95% CI 48.3%–51.9%) is below the 52.4% break-even; following every pick would have lost money.
- **Totals:** 1627-1502-45 (52.0%, 95% CI 50.2%–53.7%) is below the 52.4% break-even; following every pick would have lost money.
- Tier records are small samples. A tier being profitable in a backtest is weak evidence on its own; check whether the pattern holds across seasons and across the sensitivity variants above.
- Closing lines are sharper than the first-seen lines the live site grades against, so live results against opening/early numbers may be modestly better than this — or not. Closing line value (CLV) on the record page is the faster, less noisy signal of whether the model finds real edges.
- The model knows nothing about injuries, QB changes, weather, or rest beyond what is already in the play-by-play. Large edges are often the market pricing exactly that information.

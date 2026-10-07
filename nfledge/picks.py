"""Pick logic, edge tiers, grading and record aggregation. Shared by backtest and live ledger."""
import math

import config

BREAKEVEN_110 = 110 / 210  # 52.38%


def tier_of(edge):
    e = abs(edge)
    for name, lo, hi in config.TIERS:
        if lo <= e < hi:
            return name
    return config.TIERS[-1][0]


def make_pick(home, away, model_margin, model_total, market_home_spread, market_total):
    """Model margin is home minus away. Market spread is in betting notation for the home side."""
    out = {}
    if market_home_spread is not None and not math.isnan(market_home_spread):
        edge = model_margin + market_home_spread  # >0: home expected to cover by `edge` points
        if edge > 0:
            out.update(spread_pick=home, spread_pick_line=market_home_spread)
        elif edge < 0:
            out.update(spread_pick=away, spread_pick_line=-market_home_spread)
        else:
            out.update(spread_pick="PASS", spread_pick_line=None)
        out.update(spread_edge=round(abs(edge), 2), spread_tier=tier_of(edge))
    if market_total is not None and not math.isnan(market_total):
        edge = model_total - market_total
        out.update(total_pick="OVER" if edge > 0 else "UNDER" if edge < 0 else "PASS",
                   total_edge=round(abs(edge), 2), total_tier=tier_of(edge))
    return out


def grade_spread(pick_team, home, home_spread, home_score, away_score):
    if pick_team in (None, "PASS"):
        return None
    cover = (home_score - away_score) + home_spread  # >0 home covers
    if pick_team != home:
        cover = -cover
    return "W" if cover > 0 else "L" if cover < 0 else "P"


def grade_total(pick, total_line, home_score, away_score):
    if pick in (None, "PASS"):
        return None
    diff = (home_score + away_score) - total_line
    if pick == "UNDER":
        diff = -diff
    return "W" if diff > 0 else "L" if diff < 0 else "P"


def record(results):
    """results: iterable of 'W'/'L'/'P'. Returns summary dict incl. units at -110."""
    r = [x for x in results if x in ("W", "L", "P")]
    w, l, p = r.count("W"), r.count("L"), r.count("P")
    n = w + l
    pct = w / n if n else None
    units = w * 1.0 - l * 1.1
    se = math.sqrt(pct * (1 - pct) / n) if n else None
    # one-sided z-test vs the -110 break-even rate
    z = (pct - BREAKEVEN_110) / math.sqrt(BREAKEVEN_110 * (1 - BREAKEVEN_110) / n) if n else None
    return {
        "w": w, "l": l, "p": p, "n": n, "pct": pct, "units": units,
        "roi": units / (n * 1.1) if n else None,
        "ci_lo": pct - 1.96 * se if n else None, "ci_hi": pct + 1.96 * se if n else None,
        "z": z, "p_value": 0.5 * math.erfc(z / math.sqrt(2)) if n else None,
        "label": f"{w}-{l}" + (f"-{p}" if p else ""),
    }

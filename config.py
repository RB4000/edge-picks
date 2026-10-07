"""Single source of configuration. Rename the brand here and every page follows."""

# --- Brand ------------------------------------------------------------------
BRAND_NAME = "BRAND_NAME"
BRAND_TAGLINE = "An opponent-adjusted EPA model. Every pick timestamped, locked, and graded in public."
SITE_URL = ""  # e.g. "https://example.pages.dev" -- used for canonical/OG tags if set
DISCLAIMER = (
    "Model output is published for entertainment and informational purposes only. "
    "It is not betting advice. Past results do not guarantee future results."
)
RESPONSIBLE_GAMBLING = "If you or someone you know has a gambling problem, call 1-800-GAMBLER."

# --- Data -------------------------------------------------------------------
FIRST_SEASON = 2021          # earliest season pulled from nflverse
CURRENT_SEASON = 2026
BACKTEST_SEASONS = [2022, 2023, 2024, 2025]

# --- Model (fixed a priori; NOT tuned on the backtest) -----------------------
HALF_LIFE_WEEKS = 6.0        # within-season recency decay half-life, in weeks
PRIOR_REGRESSION = 0.6       # last season's team ratings carried into the new season (rest regresses to avg)
PRIOR_PLAYS = 300.0          # weight of the prior on each team rating, in plays (~5 games of offense)
LEAGUE_PRIOR_PLAYS = 15000.0 # weight of the prior on league-wide terms (avg EPA, HFA), ~half a season
PACE_PRIOR_GAMES = 5.0       # weight of the prior on each team's pace rating, in games
GARBAGE_WP = (0.10, 0.90)    # 4th-quarter plays outside this win-prob band are excluded from EPA

# Totals: model points are mean projections, but NFL totals skew right and market totals sit near
# the median, which produced a systematic over lean in v1.0. When enabled, model totals are shifted
# by the recency-weighted median of (model total - market total) over all games played before the
# prediction week (walk-forward; no lookahead). Margins/spreads are unaffected.
TOTALS_CALIBRATION = True

# Home field: "venue" = zero HFA when nflverse says Neutral OR the venue is international/neutral
# (nflverse marks some London "home" games as Home). "nflverse" = v1.0 behavior.
NEUTRAL_RULE = "venue"
INTERNATIONAL_STADIUM_IDS = {"LON00", "LON01", "LON02", "GER00", "MUN01", "FRA00", "MEX00", "SAO00",
                             "RIO00", "MAD01", "PAR00", "MEL00", "DUB00", "IRE00", "BER00"}
INTERNATIONAL_VENUE_WORDS = ["Tottenham", "Wembley", "Allianz", "Bayern", "Deutsche Bank Park", "Azteca",
                             "Banorte", "Corinthians", "Maracana", "Bernabeu", "Stade de France",
                             "Melbourne", "Croke", "Olympiastadion"]
NEUTRAL_OVERRIDES = {}  # game_id -> True/False, for anything the rules above get wrong

# Edge tiers, in points. Lower bound inclusive.
TIERS = [("<2", 0.0, 2.0), ("2-3.5", 2.0, 3.5), ("3.5+", 3.5, 99.0)]

# --- Market lines -------------------------------------------------------------
ODDS_API_SPORT = "americanfootball_nfl"
ODDS_API_MIN_REFRESH_HOURS = 3   # don't burn free-tier credits on rapid re-runs
TIMEZONE_DISPLAY = "America/New_York"

# Line freeze: from FREEZE_POLICY_FROM onward, a game's frozen line is the first capture at or after
# 7:00 AM Central on the Tuesday before that week's first kickoff. Earlier captures are logged as
# snapshots but never frozen.
FREEZE_POLICY_FROM = (2026, 6)
FREEZE_WEEKDAY = 1            # Monday=0, Tuesday=1
FREEZE_HOUR = 7
FREEZE_TZ = "America/Chicago"

# Prospective forward test, declared 2026-10-07 before any Week 6 line froze. In the 2022-2025 backtest,
# NFL spread picks with a raw model-vs-market disagreement of 7+ points went 35-27-2 (56.5%, 62 decided games). That
# slice was found after looking at the results, so it is tested forward only, starting Week 6 2026,
# and tallied separately. It changes no pick: these games are ordinary locked picks in the main record.
SPREAD_EXPERIMENT = {
    "name": "7+ point spread disagreements",
    "min_edge": 7.0,          # raw |model spread - frozen market spread|, as recorded in picks.csv
    "from": (2026, 6),
    "declared": "2026-10-07",
    "backtest": "35-27-2 against closing lines (56.5% of decided games)",
}

# --- NCAAF ----------------------------------------------------------------------
# Same model structure; parameters fixed a priori (not tuned on the backtest).
NCAAF_FIRST_SEASON = 2021
NCAAF_CURRENT_SEASON = 2026
NCAAF_BACKTEST_SEASONS = [2022, 2023, 2024, 2025]
NCAAF_MODEL = {
    "HALF_LIFE_WEEKS": 6.0,
    "PRIOR_REGRESSION": 0.5,      # stronger regression than NFL: transfer portal + graduation turnover
    "PRIOR_PLAYS": 300.0,         # ~4 games of college offense
    "LEAGUE_PRIOR_PLAYS": 30000.0,
    "PACE_PRIOR_GAMES": 4.0,
    "GROUP_PRIOR_PLAYS": 3000.0,  # FCS / lower-division baselines (FBS is the reference group)
    "GARBAGE_TIME": "score margin > 43/37/27/22 in Q1/Q2/Q3/Q4 (Connelly)",
    "TOTALS_CALIBRATION": True,
    "NEUTRAL_RULE": "cfbd_neutralSite",
    "HFA_METHOD": "regression",
    "N_GROUPS": 2,
    "PLAYS_DEFAULT": 70.0,
    "FIRST_SEASON": NCAAF_FIRST_SEASON,
    # v1.1 (10/7/2026): fixes the spread compression found in v1.0 (model margins SD 5 vs market 13)
    "DECAY_NORMALIZE": True,      # recency re-weights games instead of discarding evidence
    "SCALE_CALIBRATION": True,    # walk-forward margin/total scale fit on actual results
    "PRIOR_MODEL": "talent",      # preseason prior: last season + roster talent, coefficients walk-forward
    "VERSION": "cfb-epa-ridge-1.1",
}
NCAAF_ODDS_API_SPORT = "americanfootball_ncaaf"
NCAAF_FREEZE_POLICY_FROM = (2026, 7)
# College is projections-only: tested against closing lines, the model does not beat them (fitted market-blend
# weight on spreads ~0; see backtest_results.md). No NCAAF picks are made, counted or graded, and no NCAAF
# lines are captured. Weekly ratings snapshots are still written to ledger/ncaaf/ratings.
NCAAF_PROJECTIONS_ONLY = True
NCAAF_PROJECTIONS_NOTE = ("College football: projections only. We tested this model against the betting market and it "
                          "does not beat closing prices, so we publish projected scores, model lines and ratings, "
                          "but we don't make or count college picks.")
# Odds API team name -> CFBD school, for names that don't match "School Mascot" after normalising.
NCAAF_NAME_ALIASES = {
    "Hawaii Rainbow Warriors": "Hawai'i",
    "San Jose State Spartans": "San José State",
    "Southern Mississippi Golden Eagles": "Southern Miss",
    "UL Monroe Warhawks": "UL Monroe",
    "Louisiana Ragin Cajuns": "Louisiana",
    "UMass Minutemen": "Massachusetts",
    "Massachusetts Minutemen": "Massachusetts",
    "Miami (FL) Hurricanes": "Miami",
    "App State Mountaineers": "App State",
    "Appalachian State Mountaineers": "App State",
    "Sam Houston State Bearkats": "Sam Houston",
    "Sam Houston Bearkats": "Sam Houston",
    "Connecticut Huskies": "UConn",
    "Florida International Panthers": "Florida International",
    "FIU Panthers": "Florida International",
    "Texas El Paso Miners": "UTEP",
    "Central Florida Knights": "UCF",
    "Southern Methodist Mustangs": "SMU",
    "Nevada-Las Vegas Rebels": "UNLV",
    "Louisiana Monroe Warhawks": "UL Monroe",
}

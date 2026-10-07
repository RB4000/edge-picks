"""Team metadata and name mappings between nflverse, The Odds API and ESPN."""

TEAMS = {
    # nflverse abbr: (full name, short name, espn abbr)
    "ARI": ("Arizona Cardinals", "Cardinals", "ari"),
    "ATL": ("Atlanta Falcons", "Falcons", "atl"),
    "BAL": ("Baltimore Ravens", "Ravens", "bal"),
    "BUF": ("Buffalo Bills", "Bills", "buf"),
    "CAR": ("Carolina Panthers", "Panthers", "car"),
    "CHI": ("Chicago Bears", "Bears", "chi"),
    "CIN": ("Cincinnati Bengals", "Bengals", "cin"),
    "CLE": ("Cleveland Browns", "Browns", "cle"),
    "DAL": ("Dallas Cowboys", "Cowboys", "dal"),
    "DEN": ("Denver Broncos", "Broncos", "den"),
    "DET": ("Detroit Lions", "Lions", "det"),
    "GB": ("Green Bay Packers", "Packers", "gb"),
    "HOU": ("Houston Texans", "Texans", "hou"),
    "IND": ("Indianapolis Colts", "Colts", "ind"),
    "JAX": ("Jacksonville Jaguars", "Jaguars", "jax"),
    "KC": ("Kansas City Chiefs", "Chiefs", "kc"),
    "LV": ("Las Vegas Raiders", "Raiders", "lv"),
    "LAC": ("Los Angeles Chargers", "Chargers", "lac"),
    "LA": ("Los Angeles Rams", "Rams", "lar"),
    "MIA": ("Miami Dolphins", "Dolphins", "mia"),
    "MIN": ("Minnesota Vikings", "Vikings", "min"),
    "NE": ("New England Patriots", "Patriots", "ne"),
    "NO": ("New Orleans Saints", "Saints", "no"),
    "NYG": ("New York Giants", "Giants", "nyg"),
    "NYJ": ("New York Jets", "Jets", "nyj"),
    "PHI": ("Philadelphia Eagles", "Eagles", "phi"),
    "PIT": ("Pittsburgh Steelers", "Steelers", "pit"),
    "SEA": ("Seattle Seahawks", "Seahawks", "sea"),
    "SF": ("San Francisco 49ers", "49ers", "sf"),
    "TB": ("Tampa Bay Buccaneers", "Buccaneers", "tb"),
    "TEN": ("Tennessee Titans", "Titans", "ten"),
    "WAS": ("Washington Commanders", "Commanders", "wsh"),
}

FULL_TO_ABBR = {v[0]: k for k, v in TEAMS.items()}
ESPN_TO_ABBR = {v[2].upper(): k for k, v in TEAMS.items()}
ESPN_TO_ABBR.update({"LAR": "LA", "WSH": "WAS"})


def full_name(abbr):
    return TEAMS[abbr][0]


def short_name(abbr):
    return TEAMS[abbr][1]


def logo_url(abbr):
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{TEAMS[abbr][2]}.png"

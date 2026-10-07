"""Everything sport-specific, behind one interface the pipeline, backtest and site share."""
from datetime import timedelta
from functools import cached_property
from zoneinfo import ZoneInfo

import pandas as pd

import config
from nfledge import ledger, lines, ratings
from nfledge.teams import TEAMS, logo_url

ET = ZoneInfo("America/New_York")


class Sport:
    key = label = ""
    season = 0
    freeze_policy_from = (0, 0)
    launch_note_tpl = ("Week {week} lines were captured {days}, the day {label} picks launched. By then the market "
                       "had already moved off its Tuesday numbers, so these are not opening or Tuesday-morning lines.")

    def params(self):
        raise NotImplementedError

    # data ------------------------------------------------------------------
    def load(self, refresh=True):
        """-> (schedule with kickoff_utc/final/pickable/close_*, team_games, groups)"""
        raise NotImplementedError

    def engine(self, tg, sched, groups, params=None):
        return ratings.RatingsEngine(tg, sched, params or self.params(), groups)

    def current_week(self, sched, now):
        cur = sched[(sched["season"] == self.season) & sched["pickable"]]
        # ignore games long past kickoff that never got a result (cancelled / missing data)
        pending = cur[~cur["final"] & (cur["kickoff_utc"] > now - timedelta(days=2))]
        return int(pending["week"].min()) if len(pending) else None

    def ready_to_lock(self, sched, tg, week, now):
        raise NotImplementedError

    # display ---------------------------------------------------------------
    def name(self, t):
        return t

    def short(self, t):
        return t

    def abbr(self, t):
        return t

    def logo(self, t):
        return ""

    def rank_pool(self, season):
        return None


class NFL(Sport):
    key, label = "nfl", "NFL"
    season = config.CURRENT_SEASON
    freeze_policy_from = config.FREEZE_POLICY_FROM
    ledger = ledger.NFL

    def params(self):
        return ratings.nfl_params()

    def load(self, refresh=True):
        from nfledge import data
        s = data.load_schedules(refresh=refresh)
        t = s["gametime"].fillna("13:00")
        s["kickoff_utc"] = pd.to_datetime(s["gameday"] + " " + t).dt.tz_localize(ET).dt.tz_convert("UTC")
        s["pickable"] = True
        tg = data.load_team_games(s, refresh_current=refresh)
        return s, tg, None

    def feed(self, sched):
        return lines.nfl_feed()

    def ready_to_lock(self, sched, tg, week, now):
        prior = sched[(sched["season"] == self.season) & (sched["week"] < week)]
        if not prior["final"].all():
            return False, "earlier games not final yet"
        missing = set(prior["game_id"]) - set(tg["game_id"])
        if missing:
            return False, f"play-by-play not yet published for {len(missing)} game(s): {sorted(missing)[:3]}"
        return True, ""

    def name(self, t):
        return TEAMS[t][0]

    def short(self, t):
        return TEAMS[t][1]

    def logo(self, t):
        return logo_url(t)


class NCAAF(Sport):
    key, label = "ncaaf", "NCAAF"
    season = config.NCAAF_CURRENT_SEASON
    freeze_policy_from = config.NCAAF_FREEZE_POLICY_FROM
    coverage_from = config.NCAAF_COVERAGE_FROM
    ledger = ledger.NCAAF_PREVIEW if config.NCAAF_PREVIEW else ledger.NCAAF

    def params(self):
        return ratings.ncaaf_params()

    @cached_property
    def teams(self):
        from nfledge import cfb_data
        return cfb_data.load_teams(self.season)

    def load(self, refresh=True):
        from nfledge import cfb_data
        s = cfb_data.load_schedules(refresh=refresh)
        start = pd.Timestamp(config.NCAAF_COVERAGE_START_ET, tz=ET).tz_convert("UTC")
        s.loc[(s["season"] == self.season) & (s["kickoff_utc"] < start), "pickable"] = False
        tg = cfb_data.load_team_games(s, refresh=refresh)
        return s, tg, cfb_data.groups_by_season(s)

    def feed(self, sched):
        return lines.ncaaf_feed(self.teams)

    def engine(self, tg, sched, groups, params=None):
        from nfledge import cfb_data
        params = params or self.params()
        talent = cfb_data.load_talent(sorted(set(tg["season"]))) if params.get("PRIOR_MODEL") == "talent" else None
        return ratings.RatingsEngine(tg, sched, params, groups, talent)

    def ready_to_lock(self, sched, tg, week, now):
        """Earlier FBS games final, and play-by-play present for >= 95% of last week's FBS-vs-FBS games."""
        cur = sched[(sched["season"] == self.season) & sched["pickable"]]
        prior = cur[(cur["week"] < week) & (cur["kickoff_utc"] > now - timedelta(days=21))]
        open_ = prior[~prior["final"] & (prior["kickoff_utc"] > now - timedelta(days=2))]
        if len(open_):
            return False, f"{len(open_)} earlier FBS game(s) not final yet"
        last = cur[(cur["week"] == week - 1) & cur["final"]]
        if len(last):
            cov = last["game_id"].isin(tg["game_id"]).mean()
            if cov < 0.95:
                return False, f"play-by-play for week {week - 1} only {cov:.0%} complete"
        return True, ""

    def _row(self, t):
        return self.teams.loc[t] if t in self.teams.index else None

    def short(self, t):
        return t

    def name(self, t):
        return t

    def abbr(self, t):
        r = self._row(t)
        return (r["abbreviation"] if r is not None and isinstance(r["abbreviation"], str) else t[:4].upper())

    def logo(self, t):
        r = self._row(t)
        return r["logo"] if r is not None else ""

    def rank_pool(self, season):
        return set(self.teams.index[self.teams["classification"] == "fbs"])


SPORTS = [NFL(), NCAAF()]
BY_KEY = {s.key: s for s in SPORTS}

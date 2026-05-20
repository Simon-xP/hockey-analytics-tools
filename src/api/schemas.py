"""Pydantic response models for the PuckAgent API."""

from __future__ import annotations

from pydantic import BaseModel


# ── Shared ──────────────────────────────────────────────────


class TeamInfo(BaseModel):
    id: int
    abbrev: str
    name: str
    record: str | None = None


class PlayerInfo(BaseModel):
    nhl_id: int
    name: str
    position: str | None = None
    team: str | None = None
    is_goalie: bool | None = None
    headshot: str | None = None


class StatsPerGP(BaseModel):
    goals: float = 0
    assists: float = 0
    shots: float = 0
    hits: float = 0
    blocks: float = 0
    pp_goals: float | None = None
    pp_assists: float | None = None


# ── Dashboard ───────────────────────────────────────────────


class GameResponse(BaseModel):
    game_id: int
    date: str
    start_time_utc: str | None = None
    home_team: TeamInfo | None = None
    away_team: TeamInfo | None = None
    home_score: int | None = None
    away_score: int | None = None


class TodayGamesResponse(BaseModel):
    date: str
    games: list[GameResponse]


class StandingsEntry(BaseModel):
    team_id: int
    abbrev: str
    name: str
    wins: int
    losses: int
    gp: int
    gf: int
    ga: int


class StandingsResponse(BaseModel):
    standings: list[StandingsEntry]


class ScheduleTeam(BaseModel):
    team: str
    games: int
    days: list[bool]


class ScheduleOutlookResponse(BaseModel):
    week_start: str
    week_end: str
    day_labels: list[str]
    teams: list[ScheduleTeam]


class RegressionCandidate(BaseModel):
    nhl_id: int
    name: str
    position: str | None = None
    team: str | None = None
    gp: int
    goals_per_60: float
    ixg_per_60: float
    goal_diff: float
    sh_pct: float
    avg_toi: float


class RegressionResponse(BaseModel):
    buy_low: list[RegressionCandidate]
    sell_high: list[RegressionCandidate]


class OptimalAddPlayer(BaseModel):
    nhl_id: int
    name: str
    position: str | None = None
    team: str | None = None
    gp: int
    avg_toi: float
    fpts_per_gp: float
    stats_per_gp: StatsPerGP


class OptimalAddsResponse(BaseModel):
    players: list[OptimalAddPlayer]
    scoring: dict[str, float]


# ── Players ─────────────────────────────────────────────────


class PlayerSearchResult(BaseModel):
    results: list[PlayerInfo]


class GameLog(BaseModel):
    date: str
    opponent: str | None = None
    is_home: bool
    toi: float
    goals: int
    assists: int
    shots: int
    hits: int
    blocks: int
    ixg: float
    cf: int | None = None
    ca: int | None = None
    gf: int | None = None
    ga: int | None = None
    ipp: float | None = None


class GoalieSeasonStats(BaseModel):
    gp: int | None = None
    wins: int | None = None
    losses: int | None = None
    otl: int | None = None
    gaa: float | None = None
    sv_pct: float | None = None
    shutouts: int | None = None


class GoalieStats(BaseModel):
    season: GoalieSeasonStats
    career: GoalieSeasonStats


class PlayerDetailResponse(BaseModel):
    player: PlayerInfo
    recent_games: list[GameLog]
    goalie_stats: GoalieStats | None = None


class ForecastProjection(BaseModel):
    goals: float
    assists: float
    shots: float
    hits: float
    blocks: float
    fpts: float


class SituationBreakdown(BaseModel):
    toi_min: float
    rates: dict[str, float]


class ForecastResponse(BaseModel):
    nhl_id: int
    name: str
    game_date: str
    opponent: str | None = None
    is_home: bool
    projection: ForecastProjection
    situation_breakdown: dict[str, SituationBreakdown]


# ── Goalie Matchups ─────────────────────────────────────────


class GoalieMatchupTeam(BaseModel):
    team_id: int
    abbrev: str
    name: str
    gp: int
    goals_per_game: float
    goals_against_per_game: float
    opp_goalie_fpts_avg: float
    opp_goalie_win_pct: float
    shutout_pct: float
    rank: int | None = None
    total_teams: int | None = None


class GoalieMatchupRankingsResponse(BaseModel):
    rankings: list[GoalieMatchupTeam]
    scoring: dict[str, float]
    note: str


# ── News & Injuries ─────────────────────────────────────────


class GoalieStartEntry(BaseModel, extra="allow"):
    pass


class GoalieStartsResponse(BaseModel):
    games: list[dict]


class StreamableGoalie(BaseModel):
    name: str
    team: str | None = None
    team_slug: str | None = None
    opponent: str | None = None
    opponent_slug: str | None = None
    opponent_abbrev: str | None = None
    is_home: bool
    confirmation: str | None = None
    sv_pct: str | None = None
    gaa: str | None = None
    wins: str | None = None
    losses: str | None = None
    stream_score: float | None = None
    opp_goals_per_game: float | None = None
    opp_ga_per_game: float | None = None
    nhl_id: int | None = None


class StreamableGoaliesResponse(BaseModel):
    goalies: list[StreamableGoalie | dict]
    filtered: bool


class InjuryItem(BaseModel, extra="allow"):
    nhl_id: int | None = None
    player_name: str | None = None
    team_abbrev: str | None = None
    position: str | None = None
    injury_status: str | None = None
    severity: str | None = None
    body_part: str | None = None
    summary: str | None = None
    soonest_return: str | None = None
    latest_return: str | None = None
    headshot: str | None = None
    fpts_per_gp: float | None = None
    avg_toi: float | None = None
    gp: int | None = None
    roster_status: str | None = None


class InjuriesResponse(BaseModel):
    items: list[InjuryItem]


class TeamInjuryEntry(BaseModel):
    player: str
    position: str | None = None
    injury_status: str | None = None
    game_time_decision: bool = False
    news: str | None = None


class TeamInjuriesResponse(BaseModel):
    team: str
    team_abbrev: str
    injuries: list[TeamInjuryEntry]


class SnippetPlayer(BaseModel):
    name: str | None = None
    nhl_id: int | None = None
    headshot: str | None = None


class NewsSnippet(BaseModel, extra="allow"):
    category: str | None = None
    category_label: str | None = None
    category_color: str | None = None
    summary: str | None = None
    player_name: str | None = None
    player: SnippetPlayer | None = None


class NewsItemResponse(BaseModel, extra="allow"):
    source_handle: str | None = None
    text: str | None = None
    created_at: str | None = None
    snippets: list[NewsSnippet] = []


class NewsFeedResponse(BaseModel):
    items: list[NewsItemResponse]


# ── Yahoo Fantasy ───────────────────────────────────────────


class YahooStatusResponse(BaseModel):
    connected: bool


class YahooAuthUrlResponse(BaseModel):
    auth_url: str


class YahooLeaguesResponse(BaseModel):
    leagues: list[dict]


class YahooStandingsResponse(BaseModel):
    standings: list[dict]


class YahooFreeAgentsResponse(BaseModel):
    players: list[dict]


class TrendingPlayer(BaseModel):
    player_key: str | None = None
    player_id: str | None = None
    name: str | None = None
    team: str | None = None
    position: str | None = None
    status: str | None = None
    percent_owned: int | None = None
    ownership_delta: int | None = None
    nhl_id: int | None = None


class YahooTrendingResponse(BaseModel):
    players: list[TrendingPlayer]


class YahooOptimalAddPlayer(BaseModel, extra="allow"):
    nhl_id: int | None = None
    name: str | None = None
    position: str | None = None
    team: str | None = None
    fpts_per_gp: float | None = None
    gp: int | None = None
    avg_toi: float | None = None


class YahooOptimalAddsResponse(BaseModel):
    players: list[YahooOptimalAddPlayer]


class DaySchedule(BaseModel):
    date: str
    has_game: bool
    opponent: str | None = None
    is_home: bool | None = None


class RosterPlayerResponse(BaseModel):
    name: str | None = None
    nhl_id: int | None = None
    position: str | None = None
    selected_position: str | None = None
    team: str | None = None
    status: str | None = None
    fpts_per_gp: float | None = None
    avg_toi: float | None = None
    games_this_week: int = 0
    schedule: list[DaySchedule] = []


class WeekDaySummary(BaseModel):
    date: str
    day: str
    players_playing: int
    slots_used: dict[str, int]
    open_slots: list[str]


class YahooRosterWeekResponse(BaseModel):
    team_name: str | None = None
    week_start: str
    week_end: str
    week_summary: list[WeekDaySummary]
    roster: list[RosterPlayerResponse]

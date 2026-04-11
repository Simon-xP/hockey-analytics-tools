"""Per-player, per-game, per-situation advanced stats.

Computed from game_events + player_shifts via the shift-event correlation
engine. This table replaces NST's GameIndividualStats and GameOnIceStats
with our own calculations from raw NHL play-by-play data.

One row per (player, game, situation). A player in a typical game will have
rows for "5v5", "pp", "pk", and "all".

All counting stats are raw totals for that game (not per-60 rates).
Per-60 rates are derived at query time: (stat / toi_seconds) * 3600.

## How this is computed

1. For each game event, we find all players on ice by cross-referencing
   shift start/end times with the event's timestamp.

2. Each event is classified into a situation (5v5, pp, pk, other) from
   the situationCode field. Shifts that span a situation change are split
   at the transition point.

3. Individual stats: credited to the player who performed the action
   (shooter, hitter, etc.) regardless of on-ice status.

4. On-ice stats: credited to all players whose shift overlaps the event
   time AND who are on the same team (for "for" stats) or opposing team
   (for "against" stats).

5. "all" situation rows sum across all situations for convenience.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Integer,
    String,
    ForeignKey,
    UniqueConstraint,
)

from src.core.models.base import Base


class GameAdvancedStats(Base):
    __tablename__ = "game_advanced_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False)
    player_id = Column(Integer, nullable=False)  # NHL player ID
    team_id = Column(Integer, nullable=False)
    opponent_team_id = Column(Integer)

    # Situation: "5v5", "4v4", "3v3", "pp", "pk", "other", "all"
    # "5v5" = strictly 5 skaters per side, both goalies in net
    # "4v4" = 4 skaters per side (offsetting penalties)
    # "3v3" = 3 skaters per side (overtime)
    # "pp" = player's team has more skaters (5v4, 5v3, 4v3)
    # "pk" = player's team has fewer skaters (4v5, 3v5, 3v4)
    # "other" = empty net, pulled goalie, or unusual states
    # "all" = sum of all situations
    situation = Column(String(10), nullable=False)

    # ----------------------------------------------------------------
    # Time on ice (seconds, not minutes)
    # ----------------------------------------------------------------
    toi_seconds = Column(Float, default=0)

    # ----------------------------------------------------------------
    # Individual stats (things this player directly did)
    # ----------------------------------------------------------------
    # Scoring
    goals = Column(Integer, default=0)
    assists = Column(Integer, default=0)        # primary + secondary
    first_assists = Column(Integer, default=0)  # primary assists only
    second_assists = Column(Integer, default=0)
    points = Column(Integer, default=0)         # goals + assists

    # Shooting
    shots = Column(Integer, default=0)          # shots on goal (excludes misses/blocks)
    shot_attempts = Column(Integer, default=0)  # shots + misses + blocks = iCF
    missed_shots = Column(Integer, default=0)
    blocked_shots = Column(Integer, default=0)  # player's shots that were blocked

    # Other individual
    hits = Column(Integer, default=0)
    hits_taken = Column(Integer, default=0)
    blocks = Column(Integer, default=0)  # shots this player blocked (defensive)
    giveaways = Column(Integer, default=0)
    takeaways = Column(Integer, default=0)
    penalties = Column(Integer, default=0)      # penalties committed
    penalties_drawn = Column(Integer, default=0)
    faceoff_wins = Column(Integer, default=0)
    faceoff_losses = Column(Integer, default=0)

    # Individual expected goals (sum of xG for this player's shots)
    ixg = Column(Float, default=0)

    # ----------------------------------------------------------------
    # On-ice stats (events that happened while this player was on ice)
    # These require the shift-event correlation engine.
    # "for" = by this player's team, "against" = by the opponent
    # ----------------------------------------------------------------

    # Corsi (all shot attempts: shots + misses + blocked)
    cf = Column(Integer, default=0)   # Corsi For
    ca = Column(Integer, default=0)   # Corsi Against

    # Fenwick (unblocked shot attempts: shots + misses)
    ff = Column(Integer, default=0)   # Fenwick For
    fa = Column(Integer, default=0)   # Fenwick Against

    # Shots on goal
    sf = Column(Integer, default=0)   # Shots For
    sa = Column(Integer, default=0)   # Shots Against

    # Goals
    gf = Column(Integer, default=0)   # Goals For (team goals while on ice)
    ga = Column(Integer, default=0)   # Goals Against

    # Expected goals (from our xG model, summed for all shots while on ice)
    xgf = Column(Float, default=0)    # xG For
    xga = Column(Float, default=0)    # xG Against

    # Scoring chances (shot attempts from inside the "home plate" area)
    # Defined as: |x_adj| >= 69 (inside the faceoff dots depth-wise)
    #             and |y_adj| <= 22 (between the faceoff dots width-wise)
    scf = Column(Integer, default=0)  # Scoring Chances For
    sca = Column(Integer, default=0)  # Scoring Chances Against

    # High-danger chances (shot attempts from the inner slot)
    # Defined as: distance_to_net <= 15 feet (roughly the crease + slot)
    hdcf = Column(Integer, default=0)  # High-Danger Chances For
    hdca = Column(Integer, default=0)  # High-Danger Chances Against

    # ----------------------------------------------------------------
    # Zone starts (faceoffs that start a shift or happen during a shift)
    # ----------------------------------------------------------------
    oz_starts = Column(Integer, default=0)  # Offensive zone faceoffs while on ice
    dz_starts = Column(Integer, default=0)  # Defensive zone
    nz_starts = Column(Integer, default=0)  # Neutral zone

    # ----------------------------------------------------------------
    # Derived (computed for convenience, could also be done at query time)
    # ----------------------------------------------------------------
    # Individual Points Percentage: points / gf (while on ice)
    # What fraction of team goals did this player get a point on?
    ipp = Column(Float)

    __table_args__ = (
        UniqueConstraint('game_id', 'player_id', 'situation',
                         name='uq_game_advanced_stats'),
    )

    def __repr__(self):
        return (
            f"<GameAdvancedStats {self.player_id} game={self.game_id} "
            f"{self.situation} TOI={self.toi_seconds:.0f}s>"
        )

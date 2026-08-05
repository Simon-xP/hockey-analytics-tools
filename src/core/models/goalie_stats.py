"""Per-goalie, per-game stat line.

The goalie analogue of `game_advanced_stats`. One row per goalie per game
they appeared in, built from `shot_attempts` (which carries `goalie_id` and
`xg` on every shot), `player_shifts` (who started, how long they played),
and `games` (scores, for the decision and the shutout).

Everything downstream reads this table instead of recomputing from shots.
The old on-the-fly derivation in `src/optimize/goalies.py` recomputed saves
and wins per call with per-game subqueries, which was both slow and wrong in
several ways this table fixes:

1. **Shootout shots are excluded.** Only `period <= 4` counts. NHL scoring
   does not charge shootout attempts as saves or goals against.
2. **The decision goes to the goalie of record**, not to every goalie who
   faced a shot in a win. A pulled starter does not get credit for the
   comeback.
3. **Starts and relief appearances are separated.** `is_start` comes from a
   period-1 shift beginning at 0:00, not from "faced at least one shot".
   Models train on starts only; the two populations are not the same.
4. **A shutout requires playing the whole game**, not merely allowing zero
   goals during the appearance.
5. **Empty-net goals are not charged to the goalie.** They arrive with a
   null `goalie_id` on the shot, and are counted separately in
   `empty_net_ga_team` for context.
6. **No date leakage.** This table stores facts about a completed game; the
   temporal gating happens in the query layer, which filters on
   `Game.date < as_of` the way `src/core/queries/` does.

`xga` and `gsax` are populated by the goalie-facing expected goals model
(`src/analytics/goalies/xga_model.py`), which is trained only on shots that
reached the net. The attempt-level model in `src/analytics/xg/` cannot be
used here: conditioning on "it reached the net" raises the true goal
probability well above that model's estimate, and the bias drifts by season.
Both columns are nullable so the log can be built before that model exists.

See `docs/goalie-forecasting.md`.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from src.core.models.base import Base


class GoalieGameLog(Base):
    __tablename__ = "goalie_game_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False)
    goalie_id = Column(Integer, nullable=False)  # NHL player ID
    team_id = Column(Integer, nullable=False)
    opponent_team_id = Column(Integer)
    game_date = Column(Date, nullable=False, index=True)
    season = Column(String(8), nullable=False)  # "20242025"

    # --- Appearance -----------------------------------------------------
    # is_start: had a shift beginning at 0:00 of period 1.
    # is_relief: appeared without starting.
    # A goalie who dressed but never played gets no row at all.
    is_start = Column(Boolean, nullable=False, default=False)
    is_relief = Column(Boolean, nullable=False, default=False)
    is_home = Column(Boolean)
    toi_seconds = Column(Integer)  # from shifts, regulation + OT
    played_full_game = Column(Boolean)  # never pulled, never relieved

    # --- Result ---------------------------------------------------------
    # decision: "W", "L", "OTL", or None (no decision, e.g. relief in a
    # game where the other goalie was of record).
    decision = Column(String(3))
    shutout = Column(Boolean, nullable=False, default=False)
    team_score = Column(Integer)
    opponent_score = Column(Integer)

    # --- Volume and results faced ---------------------------------------
    # shots_against counts shots on goal plus goals (a goal is a shot that
    # went in), period <= 4. saves = shots_against - goals_against.
    shots_against = Column(Integer, nullable=False, default=0)
    saves = Column(Integer, nullable=False, default=0)
    goals_against = Column(Integer, nullable=False, default=0)

    # Unblocked attempts faced, for context on workload beyond SOG.
    fenwick_against = Column(Integer)

    # Goals the team conceded into an empty net while this goalie was the
    # dressed starter. Not charged to the goalie, kept for reconciliation
    # against the official box score.
    empty_net_ga_team = Column(Integer, default=0)

    # --- Expected goals (goalie-facing model) ---------------------------
    xga = Column(Float)   # sum of P(goal | shot on goal) over shots faced
    gsax = Column(Float)  # xga - goals_against, positive is good

    # --- Danger splits --------------------------------------------------
    # Buckets are cut on the goalie-facing xGA per shot. Thresholds live in
    # src/predict/goalies/constants.py so they can be retuned in one place.
    hd_shots_against = Column(Integer, default=0)
    hd_goals_against = Column(Integer, default=0)
    hd_xga = Column(Float)
    md_shots_against = Column(Integer, default=0)
    md_goals_against = Column(Integer, default=0)
    md_xga = Column(Float)
    ld_shots_against = Column(Integer, default=0)
    ld_goals_against = Column(Integer, default=0)
    ld_xga = Column(Float)

    # --- Situation splits -----------------------------------------------
    # "ev" is even strength (the goalie's team at equal skater count),
    # "pk" is the goalie's team shorthanded, "pp" is the rarer case of
    # facing shots while the team is on the power play.
    ev_shots_against = Column(Integer, default=0)
    ev_goals_against = Column(Integer, default=0)
    pk_shots_against = Column(Integer, default=0)
    pk_goals_against = Column(Integer, default=0)
    pp_shots_against = Column(Integer, default=0)
    pp_goals_against = Column(Integer, default=0)

    # --- Fantasy --------------------------------------------------------
    # Computed with GOALIE_WEIGHTS at build time so the backtest and the
    # API read the same number. Recompute if league weights change.
    fpts = Column(Float)

    __table_args__ = (
        UniqueConstraint("game_id", "goalie_id", name="uq_goalie_game"),
        Index("ix_goalie_game_log_goalie_date", "goalie_id", "game_date"),
        Index("ix_goalie_game_log_team_date", "team_id", "game_date"),
    )

    def __repr__(self):
        role = "start" if self.is_start else "relief"
        return (
            f"<GoalieGameLog {self.goalie_id} game={self.game_id} "
            f"{role} {self.saves}/{self.shots_against}>"
        )

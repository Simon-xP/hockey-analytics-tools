"""Pluggable transaction strategies for backtesting.

Each strategy implements the same interface: given a data context,
roster, and FA pool, decide which transactions to make.

Strategies range from trivial baselines to the full forecast pipeline,
allowing apples-to-apples comparison of decision quality.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from src.backtest.data_context import BacktestDataContext
from src.core.queries.stats import StatsProvider
from src.optimize.models import (
    AggressionLevel,
    Roster,
    RosterPlayer,
    RosterSlotSettings,
    TransactionCandidate,
)


@runtime_checkable
class TransactionStrategy(Protocol):
    @property
    def name(self) -> str: ...

    def decide(
        self,
        ctx: BacktestDataContext,
        roster_nhl_ids: set[int],
        fa_pool: list[dict],
        adds_remaining: int,
        aggression: AggressionLevel,
    ) -> list[dict]:
        """Decide which FA players to pick up.

        Args:
            ctx: Temporal-gated data context.
            roster_nhl_ids: NHL IDs currently on the roster.
            fa_pool: List of FA player dicts with at least
                     {"nhl_id", "name", "position", "fpts_per_gp"}.
            adds_remaining: Max transactions allowed this period.
            aggression: How aggressively to stream.

        Returns:
            List of pickup dicts: {"nhl_id", "name", "position",
            "fpts_per_gp", "reasoning"}.
            Length <= adds_remaining.
        """
        ...


@dataclass
class BaselineStrategy:
    """Pick the highest trailing FPTS/GP players from the FA pool.

    This is the straw man — no forecasting, no slot awareness, no
    schedule optimization. Every other strategy must beat this.
    """

    top_k: int = 3

    @property
    def name(self) -> str:
        return "baseline"

    def decide(
        self,
        ctx: BacktestDataContext,
        roster_nhl_ids: set[int],
        fa_pool: list[dict],
        adds_remaining: int,
        aggression: AggressionLevel,
    ) -> list[dict]:
        picks = []
        for fa in fa_pool:
            if len(picks) >= min(self.top_k, adds_remaining):
                break
            if fa["nhl_id"] not in roster_nhl_ids:
                picks.append({
                    **fa,
                    "reasoning": f"Highest trailing FPTS/GP ({fa['fpts_per_gp']:.2f})",
                })
        return picks


@dataclass
class ScheduleAwareStrategy:
    """Pick FAs weighted by remaining games this week.

    Ranks candidates by fpts_per_gp * remaining_games_this_week,
    skips players with no games left, and spreads adds across the
    week using a budget-pacing cap.
    """

    min_remaining_games: int = 1

    @property
    def name(self) -> str:
        return "schedule_aware"

    def decide(
        self,
        ctx: BacktestDataContext,
        roster_nhl_ids: set[int],
        fa_pool: list[dict],
        adds_remaining: int,
        aggression: AggressionLevel,
    ) -> list[dict]:
        from src.core.models import Player

        as_of = ctx.as_of
        days_until_sunday = 6 - as_of.weekday()
        sunday = as_of + timedelta(days=days_until_sunday)
        days_remaining = days_until_sunday + 1

        scored = []
        for fa in fa_pool:
            nhl_id = fa["nhl_id"]
            if nhl_id in roster_nhl_ids:
                continue

            player = ctx.session.query(Player).filter(
                Player.nhl_id == nhl_id
            ).first()
            if not player or not player.team_id:
                continue

            games = ctx.schedule.get_team_games_in_range(
                player.team_id, as_of, sunday,
            )
            remaining_gp = len(games)

            if remaining_gp < self.min_remaining_games:
                continue

            weekly_value = fa["fpts_per_gp"] * remaining_gp
            scored.append({
                **fa,
                "remaining_gp": remaining_gp,
                "weekly_value": weekly_value,
            })

        scored.sort(key=lambda x: x["weekly_value"], reverse=True)

        if not scored:
            return []

        max_today = self._max_adds_today(
            adds_remaining, days_remaining, scored,
        )

        picks = []
        for s in scored[:max_today]:
            picks.append({
                "nhl_id": s["nhl_id"],
                "name": s["name"],
                "position": s.get("position", "F"),
                "fpts_per_gp": s["fpts_per_gp"],
                "weekly_fpts": s["weekly_value"],
                "reasoning": (
                    f"Schedule: {s['remaining_gp']}GP left, "
                    f"ev={s['weekly_value']:.1f} "
                    f"({s['fpts_per_gp']:.2f}/GP × {s['remaining_gp']}GP)"
                ),
            })

        return picks

    def _max_adds_today(
        self,
        adds_remaining: int,
        days_remaining: int,
        scored: list[dict],
    ) -> int:
        """Budget-pace adds across the week.

        Uses ceil(adds_remaining / days_remaining) as the base rate,
        but allows an extra add when the top candidate's weekly_value
        is at least 2x the pool median.
        """
        base = math.ceil(adds_remaining / days_remaining)

        if len(scored) >= 2:
            median_val = scored[len(scored) // 2]["weekly_value"]
            if median_val > 0 and scored[0]["weekly_value"] >= 2 * median_val:
                base = min(base + 1, adds_remaining)

        return min(base, len(scored))


@dataclass
class SimpleValueStrategy:
    """Uses compute_player_value_simple + plan_week.

    This is what the existing backtest.py does — historical FPTS/GP
    with slot awareness, drop ranking, and aggression weighting.
    Wraps the full weekly_optimizer pipeline through the data context.
    """

    fa_pool_size: int = 50
    max_drop_candidates: int = 8

    @property
    def name(self) -> str:
        return "simple_value"

    def decide(
        self,
        ctx: BacktestDataContext,
        roster_nhl_ids: set[int],
        fa_pool: list[dict],
        adds_remaining: int,
        aggression: AggressionLevel,
    ) -> list[dict]:
        from src.optimize.value import (
            find_optimal_window_simple,
            compute_player_value_simple,
        )
        from src.optimize.replacement import compute_replacement_level
        from src.optimize.drops import get_drop_candidates
        from src.optimize.week.heavy import plan_week

        schedule_range = ctx.schedule.get_week_date_range(
            self._find_yahoo_week(ctx)
        )
        if not schedule_range:
            return []

        monday, sunday = schedule_range
        yahoo_week = self._find_yahoo_week(ctx)

        roster = self._build_roster_from_ids(ctx, roster_nhl_ids)
        if not roster.players:
            return []

        fa_values = []
        for fa in fa_pool[:self.fa_pool_size]:
            nhl_id = fa.get("nhl_id")
            if not nhl_id:
                continue
            pv = find_optimal_window_simple(
                ctx.session, nhl_id, roster, ctx.as_of,
                max_window_days=7, season="20252026",
                as_of=ctx.as_of,
                skip_upside=True,
            )
            if pv and pv.weekly_fpts > 0:
                fa_values.append(pv)

        fa_values.sort(key=lambda x: x.weekly_fpts, reverse=True)

        if not fa_values:
            return []

        fa_dicts = [
            {"name": pv.name, "team": pv.team, "position": ",".join(pv.positions)}
            for pv in fa_values[:30]
        ]
        repl = compute_replacement_level(
            ctx.session, fa_dicts, top_n=5, as_of=ctx.as_of,
        )

        drops = get_drop_candidates(
            ctx.session, roster, yahoo_week, repl,
            max_candidates=self.max_drop_candidates,
            as_of=ctx.as_of,
        )

        if not drops:
            return []

        plan = plan_week(
            roster=roster,
            add_targets=fa_values,
            drop_candidates=drops,
            yahoo_week=yahoo_week,
            replacement=repl,
            adds_remaining=adds_remaining,
            aggression=aggression,
            sim_date=ctx.as_of,
        )

        picks = []
        for txn in plan.transactions:
            picks.append({
                "nhl_id": txn.add_player.nhl_id,
                "name": txn.add_player.name,
                "position": ",".join(txn.add_player.positions),
                "fpts_per_gp": txn.add_player.fpts_per_game,
                "weekly_fpts": txn.add_player.weekly_fpts,
                "drop_nhl_id": txn.drop_player.nhl_id if txn.drop_player else None,
                "drop_name": txn.drop_player.name if txn.drop_player else None,
                "adjusted_score": txn.adjusted_score,
                "reasoning": "; ".join(txn.reasoning),
            })
        return picks

    def _find_yahoo_week(self, ctx: BacktestDataContext) -> int:
        """Find the Yahoo week that contains the as_of date."""
        from src.core.models import Game
        game = (
            ctx.session.query(Game)
            .filter(Game.date >= ctx.as_of)
            .order_by(Game.date)
            .first()
        )
        return game.yahoo_week if game else 1

    def _build_roster_from_ids(
        self, ctx: BacktestDataContext, roster_nhl_ids: set[int],
    ) -> Roster:
        """Build a Roster from a set of NHL player IDs."""
        from src.core.models import Player

        players = []
        for nhl_id in roster_nhl_ids:
            player = ctx.session.query(Player).filter(
                Player.nhl_id == nhl_id
            ).first()
            if not player:
                continue

            positions = (
                player.yahoo_positions.split(",")
                if player.yahoo_positions
                else [player.position or "F"]
            )
            positions = [pos.strip() for pos in positions if pos.strip()]

            team_abbrev = ""
            if player.team:
                team_abbrev = player.team.abbrev

            players.append(
                RosterPlayer(
                    name=player.full_name,
                    team=team_abbrev,
                    positions=positions or ["F"],
                    nhl_id=nhl_id,
                )
            )

        return Roster(players=players, roster_slot_settings=RosterSlotSettings())


@dataclass
class OracleStrategy:
    """Pick FAs by actual future FPTS — the ceiling any strategy can hit.

    Uses real outcomes to sanity-check the evaluation pipeline:
    if the oracle doesn't produce strongly positive pickup value,
    something is wrong with scoring.
    """

    lookahead_days: int = 7

    @property
    def name(self) -> str:
        return "oracle"

    def decide(
        self,
        ctx: BacktestDataContext,
        roster_nhl_ids: set[int],
        fa_pool: list[dict],
        adds_remaining: int,
        aggression: AggressionLevel,
    ) -> list[dict]:
        outcome_stats = StatsProvider(session=ctx.session, as_of=date.max)
        end = ctx.as_of + timedelta(days=self.lookahead_days)

        scored = []
        for fa in fa_pool:
            nhl_id = fa.get("nhl_id")
            if not nhl_id or nhl_id in roster_nhl_ids:
                continue
            actual = outcome_stats.get_actual_fpts_in_range(
                nhl_id, ctx.as_of, end,
            )
            if actual["gp"] > 0:
                scored.append({
                    **fa,
                    "actual_fpts": actual["total_fpts"],
                    "actual_gp": actual["gp"],
                })

        scored.sort(key=lambda x: x["actual_fpts"], reverse=True)

        picks = []
        for s in scored[:min(1, adds_remaining)]:
            picks.append({
                "nhl_id": s["nhl_id"],
                "name": s["name"],
                "position": s.get("position", "F"),
                "fpts_per_gp": s["actual_fpts"] / s["actual_gp"],
                "weekly_fpts": s["actual_fpts"],
                "reasoning": (
                    f"Oracle: {s['actual_fpts']:.1f} FPTS in "
                    f"{s['actual_gp']}GP over next {self.lookahead_days}d"
                ),
            })

        return picks


@dataclass
class PuckAgentStrategy:
    """Full PuckAgent strategy — roster valuation + 5-stage decision.

    Computes RosterPlayerState for each player, builds FA candidates
    with schedule and upside data, then runs the core decision engine.
    """

    upside_weight: float = 0.15
    must_fire_floor: float = 0.1
    min_remaining_games: int = 1

    @property
    def name(self) -> str:
        return "puck_agent"

    def decide(
        self,
        ctx: BacktestDataContext,
        roster_nhl_ids: set[int],
        fa_pool: list[dict],
        adds_remaining: int,
        aggression: AggressionLevel,
    ) -> list[dict]:
        from src.core.models import Player
        from src.optimize.roster_state import compute_roster_state
        from src.optimize.daily import (
            FACandidate,
            StrategyConfig,
            decide as strategy_decide,
        )
        from src.predict.signals.upside import compute_upside_score

        as_of = ctx.as_of
        days_until_sunday = 6 - as_of.weekday()
        sunday = as_of + timedelta(days=days_until_sunday)
        days_remaining = days_until_sunday + 1

        # Phase A: Build roster state
        roster_state = compute_roster_state(
            ctx.session, roster_nhl_ids, as_of, week_end=sunday,
        )

        if not roster_state:
            return []

        # Build FA candidates with schedule + upside
        fa_candidates = []
        for fa in fa_pool:
            nhl_id = fa["nhl_id"]
            if nhl_id in roster_nhl_ids:
                continue

            player = ctx.session.query(Player).filter(
                Player.nhl_id == nhl_id,
            ).first()
            if not player or not player.team_id:
                continue

            games = ctx.schedule.get_team_games_in_range(
                player.team_id, as_of, sunday,
            )
            remaining_gp = len(games)

            upside = compute_upside_score(
                ctx.session, nhl_id, as_of=as_of,
            )

            team_abbrev = player.team.abbrev if player.team else ""

            fa_candidates.append(FACandidate(
                nhl_id=nhl_id,
                name=fa["name"],
                positions=fa.get("position", "F").split(","),
                team_id=player.team_id,
                team_abbrev=team_abbrev,
                fpts_per_gp=fa["fpts_per_gp"],
                remaining_games_this_week=remaining_gp,
                upside_score=upside,
            ))

        # Phase B: Run decision engine
        config = StrategyConfig(
            upside_weight=self.upside_weight,
            must_fire_floor=self.must_fire_floor,
            min_remaining_games=self.min_remaining_games,
        )

        transactions = strategy_decide(
            roster=roster_state,
            fa_pool=fa_candidates,
            adds_remaining=adds_remaining,
            days_remaining_in_week=days_remaining,
            aggression=aggression,
            config=config,
        )

        # Convert to engine-compatible format
        picks = []
        for txn in transactions:
            picks.append({
                "nhl_id": txn.add.nhl_id,
                "name": txn.add.name,
                "position": ",".join(txn.add.positions),
                "fpts_per_gp": txn.add.fpts_per_gp,
                "weekly_fpts": txn.add_value,
                "drop_nhl_id": txn.drop.nhl_id if txn.drop else None,
                "drop_name": txn.drop.name if txn.drop else None,
                "adjusted_score": txn.net_value,
                "reasoning": txn.reasoning,
            })
        return picks

"""Walk-forward transaction backtest using real Yahoo league data.

Uses synced Yahoo draft + transaction data to reconstruct:
- Your roster at any point in time
- The FA pool at any point in time

For each week:
1. Reconstruct roster as-of Monday from Yahoo data
2. Reconstruct FA pool from Yahoo data
3. Run the optimizer to generate a WeekPlan
4. Compare to what you actually did that week
5. Score both against actual FPTS outcomes

This lets us measure: "Would the agent have done better than me?"
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.db import get_session
from src.core.models import (
    Game, GameAdvancedStats, Player, Team,
    YahooDraftPick, YahooTransaction,
)
from src.core.resolver import resolve_player
from src.ingest.yahoo.sync import get_my_roster_at, get_free_agents_at
from src.tools.fantasy.scoring import SKATER_WEIGHTS
from src.tools.schedule.models import Roster, RosterPlayer, RosterSlotSettings
from src.tools.transactions.models import (
    AggressionLevel,
    PlayerValue,
    PlayerType,
    ReplacementLevel,
    WeekPlan,
)
from src.tools.transactions.player_value import (
    compute_player_value_simple,
    find_optimal_window_simple,
)
from src.tools.transactions.replacement_level import compute_replacement_level
from src.tools.transactions.drop_ranker import get_drop_candidates
from src.tools.transactions.weekly_optimizer import optimize_week


@dataclass
class WeekBacktestResult:
    """Result for a single week of backtesting."""

    yahoo_week: int
    week_start: datetime

    # Agent's plan
    agent_plan: WeekPlan
    agent_adds: list[str] = field(default_factory=list)  # player names
    agent_drops: list[str] = field(default_factory=list)

    # What you actually did
    actual_adds: list[str] = field(default_factory=list)
    actual_drops: list[str] = field(default_factory=list)

    # FPTS outcomes
    agent_add_fpts: float = 0.0  # actual FPTS from agent's adds
    agent_drop_fpts: float = 0.0  # actual FPTS from agent's drops (what we'd lose)
    your_add_fpts: float = 0.0  # actual FPTS from your adds
    your_drop_fpts: float = 0.0  # actual FPTS from your drops

    # Net results
    agent_net: float = 0.0  # agent_add - agent_drop
    your_net: float = 0.0  # your_add - your_drop
    edge: float = 0.0  # agent_net - your_net (positive = agent did better)


@dataclass
class BacktestResult:
    """Full backtest result across all weeks."""

    league_key: str
    team_name: str
    start_week: int
    end_week: int

    # Totals
    agent_total_adds: int = 0
    your_total_adds: int = 0
    agent_total_fpts: float = 0.0
    your_total_fpts: float = 0.0
    total_edge: float = 0.0  # positive = agent outperformed

    weekly_results: list[WeekBacktestResult] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Backtest: {self.team_name} weeks {self.start_week}-{self.end_week}",
            f"",
            f"  Agent adds: {self.agent_total_adds}",
            f"  Your adds:  {self.your_total_adds}",
            f"",
            f"  Agent net FPTS: {self.agent_total_fpts:+.1f}",
            f"  Your net FPTS:  {self.your_total_fpts:+.1f}",
            f"  Edge (agent - you): {self.total_edge:+.1f}",
            f"",
            "Weekly breakdown:",
        ]
        for wr in self.weekly_results:
            agent_adds_str = ", ".join(wr.agent_adds[:3]) or "none"
            your_adds_str = ", ".join(wr.actual_adds[:3]) or "none"
            lines.append(
                f"  Week {wr.yahoo_week}: "
                f"agent={wr.agent_net:+.1f} ({len(wr.agent_adds)} adds), "
                f"you={wr.your_net:+.1f} ({len(wr.actual_adds)} adds), "
                f"edge={wr.edge:+.1f}"
            )
            if wr.agent_adds:
                lines.append(f"    Agent picked: {agent_adds_str}")
            if wr.actual_adds:
                lines.append(f"    You picked:   {your_adds_str}")
        return "\n".join(lines)


def _get_week_date_range(session: Session, yahoo_week: int) -> tuple[datetime, datetime]:
    """Get the Monday and Sunday of a Yahoo fantasy week."""
    game = (
        session.query(Game)
        .filter(Game.yahoo_week == yahoo_week)
        .order_by(Game.date)
        .first()
    )
    if not game:
        return None, None

    # Find Monday of that week
    game_date = game.date
    days_since_monday = game_date.weekday()
    monday = datetime.combine(game_date - timedelta(days=days_since_monday), datetime.min.time())
    sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return monday, sunday


def _compute_actual_weekly_fpts(
    session: Session,
    nhl_id: int,
    yahoo_week: int,
) -> float:
    """Compute actual FPTS a player produced in a given week.

    Uses GameAdvancedStats (2025-26 data) to get actual stats.
    """
    stats = (
        session.query(GameAdvancedStats)
        .join(Game, GameAdvancedStats.game_id == Game.game_id)
        .filter(
            GameAdvancedStats.player_id == nhl_id,
            GameAdvancedStats.situation == "all",  # use aggregated stats
            Game.yahoo_week == yahoo_week,
        )
        .all()
    )

    total_fpts = 0.0
    for gs in stats:
        # Note: GameAdvancedStats has:
        # - blocked_shots = player's shots that were blocked (bad)
        # - blocks = shots this player blocked (good, fantasy points)
        # - penalties = number of penalties (approx 2 PIM each)
        fpts = (
            (gs.goals or 0) * SKATER_WEIGHTS.get("goals", 3)
            + (gs.assists or 0) * SKATER_WEIGHTS.get("assists", 2)
            + (gs.shots or 0) * SKATER_WEIGHTS.get("shots", 0.3)
            + (gs.hits or 0) * SKATER_WEIGHTS.get("hits", 0.4)
            + (gs.blocks or 0) * SKATER_WEIGHTS.get("blocks", 0.5)
            + ((gs.penalties or 0) * 2) * SKATER_WEIGHTS.get("pim", 0.3)  # ~2 PIM per penalty
        )
        total_fpts += fpts

    return total_fpts


def _yahoo_roster_to_roster(
    yahoo_roster: list[dict],
    session: Session,
    roster_settings: RosterSlotSettings,
) -> Roster:
    """Convert Yahoo roster dict to our Roster model."""
    players = []
    for p in yahoo_roster:
        nhl_id = p.get("nhl_id")
        if not nhl_id:
            continue

        player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
        if not player:
            continue

        positions = p.get("position", "").split(",") if p.get("position") else []
        positions = [pos.strip() for pos in positions if pos.strip()]

        players.append(
            RosterPlayer(
                name=p["player_name"],
                team=p.get("nhl_team", ""),
                positions=positions or ["F"],  # default to F if no positions
                nhl_id=nhl_id,
            )
        )

    return Roster(players=players, roster_slot_settings=roster_settings)


def _fa_pool_to_player_values(
    fa_pool: list[dict],
    yahoo_week: int,
    session: Session,
    roster: Roster | None = None,
    as_of: date | None = None,
) -> list[PlayerValue]:
    """Convert Yahoo FA pool to PlayerValue list for the optimizer.

    If roster and as_of are provided, uses window-based optimization
    with slot checking. Otherwise falls back to simple week-based valuation.
    """
    values = []
    for fa in fa_pool:
        nhl_id = fa.get("nhl_id")
        if not nhl_id:
            continue

        if roster and as_of:
            # Window-based with slot checking; as_of is both the window
            # start and the knowledge cutoff for historical stats.
            pv = find_optimal_window_simple(
                session, nhl_id, roster, as_of,
                max_window_days=7, season="20252026",
                as_of=as_of,
            )
        else:
            # Fallback to simple week-based
            pv = compute_player_value_simple(
                session, nhl_id, yahoo_week, as_of=as_of,
            )

        if pv and pv.weekly_fpts > 0:
            values.append(pv)

    values.sort(key=lambda x: x.weekly_fpts, reverse=True)
    return values


def _get_your_transactions(
    session: Session,
    league_key: str,
    team_name: str,
    week_start: datetime,
    week_end: datetime,
) -> tuple[list[dict], list[dict]]:
    """Get the adds and drops you actually made during a week."""
    transactions = (
        session.query(YahooTransaction)
        .filter(
            YahooTransaction.league_key == league_key,
            YahooTransaction.fantasy_team_name == team_name,
            YahooTransaction.timestamp >= week_start,
            YahooTransaction.timestamp <= week_end,
        )
        .order_by(YahooTransaction.timestamp)
        .all()
    )

    adds = []
    drops = []
    for tx in transactions:
        info = {
            "yahoo_player_id": tx.yahoo_player_id,
            "player_name": tx.player_name,
            "nhl_id": tx.nhl_id,
            "position": tx.position,
        }
        if tx.action == "add":
            adds.append(info)
        elif tx.action == "drop":
            drops.append(info)

    return adds, drops


class TransactionBacktester:
    """Walk-forward transaction simulator using real Yahoo league data.

    Compares the agent's recommended transactions against what you
    actually did, and scores both against actual outcomes.
    """

    def __init__(
        self,
        league_key: str,
        team_name: str,
        adds_per_week: int = 4,
        aggression: AggressionLevel = AggressionLevel.NORMAL,
        fa_pool_size: int = 50,
    ):
        self.league_key = league_key
        self.team_name = team_name
        self.adds_per_week = adds_per_week
        self.aggression = aggression
        self.fa_pool_size = fa_pool_size

        # Default roster settings (Yahoo 16-team league)
        self.roster_settings = RosterSlotSettings(
            c=2,
            lw=2,
            rw=2,
            d=4,
            g=2,
            util=2,
            bn=4,
            ir=2,
        )

    def run(
        self,
        start_week: int,
        end_week: int,
    ) -> BacktestResult:
        """Run backtest across a range of Yahoo fantasy weeks.

        For each week:
        1. Reconstruct roster and FA pool as-of Monday
        2. Run agent to get recommended transactions
        3. Get your actual transactions that week
        4. Score both against actual FPTS outcomes
        """
        weekly_results: list[WeekBacktestResult] = []

        with get_session() as session:
            for week in range(start_week, end_week + 1):
                print(f"Backtesting week {week}...")

                # Get date range for this week
                week_start, week_end = _get_week_date_range(session, week)
                if not week_start:
                    print(f"  No games in week {week}, skipping")
                    continue

                # Reconstruct roster as-of Monday morning
                yahoo_roster = get_my_roster_at(
                    self.league_key, self.team_name, week_start, session
                )
                roster = _yahoo_roster_to_roster(
                    yahoo_roster, session, self.roster_settings
                )

                if not roster.players:
                    print(f"  Empty roster for week {week}, skipping")
                    continue

                # Get FA pool as-of Monday with window-based valuation
                yahoo_fas = get_free_agents_at(
                    self.league_key, week_start, session
                )
                fa_pool = _fa_pool_to_player_values(
                    yahoo_fas, week, session,
                    roster=roster,
                    as_of=week_start.date(),  # knowledge cutoff
                )
                fa_pool = fa_pool[:self.fa_pool_size]

                if not fa_pool:
                    print(f"  No FAs available for week {week}")
                    weekly_results.append(
                        WeekBacktestResult(
                            yahoo_week=week,
                            week_start=week_start,
                            agent_plan=WeekPlan(
                                yahoo_week=week,
                                transactions=[],
                                adds_used=0,
                                projected_fpts_gain=0.0,
                                aggression=self.aggression,
                            ),
                        )
                    )
                    continue

                # Compute replacement level
                fa_dicts = [
                    {"name": pv.name, "team": pv.team, "position": ",".join(pv.positions)}
                    for pv in fa_pool[:30]
                ]
                repl = compute_replacement_level(
                    session, fa_dicts, top_n=5, as_of=week_start.date(),
                )

                # Get drop candidates from roster (with correct ROS date)
                drops = get_drop_candidates(
                    session, roster, week, repl, max_candidates=8,
                    as_of=week_start.date(),
                )

                # Run optimizer
                if drops:
                    plan = optimize_week(
                        roster=roster,
                        add_targets=fa_pool,
                        drop_candidates=drops,
                        yahoo_week=week,
                        replacement=repl,
                        adds_remaining=self.adds_per_week,
                        aggression=self.aggression,
                        sim_date=week_start.date(),  # backtest uses simulated date
                    )
                else:
                    plan = WeekPlan(
                        yahoo_week=week,
                        transactions=[],
                        adds_used=0,
                        projected_fpts_gain=0.0,
                        aggression=self.aggression,
                    )

                # Get your actual transactions
                your_adds, your_drops = _get_your_transactions(
                    session, self.league_key, self.team_name,
                    week_start, week_end
                )

                # Score agent's picks
                agent_add_fpts = 0.0
                agent_drop_fpts = 0.0
                agent_add_names = []
                agent_drop_names = []

                for txn in plan.transactions:
                    add_fpts = _compute_actual_weekly_fpts(
                        session, txn.add_player.nhl_id, week
                    )
                    agent_add_fpts += add_fpts
                    agent_add_names.append(txn.add_player.name)

                    if txn.drop_player:
                        drop_fpts = _compute_actual_weekly_fpts(
                            session, txn.drop_player.nhl_id, week
                        )
                        agent_drop_fpts += drop_fpts
                        agent_drop_names.append(txn.drop_player.name)

                # Score your picks
                your_add_fpts = 0.0
                your_drop_fpts = 0.0
                your_add_names = []
                your_drop_names = []

                for add in your_adds:
                    if add["nhl_id"]:
                        fpts = _compute_actual_weekly_fpts(
                            session, add["nhl_id"], week
                        )
                        your_add_fpts += fpts
                    your_add_names.append(add["player_name"])

                for drop in your_drops:
                    if drop["nhl_id"]:
                        fpts = _compute_actual_weekly_fpts(
                            session, drop["nhl_id"], week
                        )
                        your_drop_fpts += fpts
                    your_drop_names.append(drop["player_name"])

                agent_net = agent_add_fpts - agent_drop_fpts
                your_net = your_add_fpts - your_drop_fpts
                edge = agent_net - your_net

                week_result = WeekBacktestResult(
                    yahoo_week=week,
                    week_start=week_start,
                    agent_plan=plan,
                    agent_adds=agent_add_names,
                    agent_drops=agent_drop_names,
                    actual_adds=your_add_names,
                    actual_drops=your_drop_names,
                    agent_add_fpts=agent_add_fpts,
                    agent_drop_fpts=agent_drop_fpts,
                    your_add_fpts=your_add_fpts,
                    your_drop_fpts=your_drop_fpts,
                    agent_net=agent_net,
                    your_net=your_net,
                    edge=edge,
                )
                weekly_results.append(week_result)

                print(
                    f"  Agent: {len(agent_add_names)} adds, net {agent_net:+.1f} | "
                    f"You: {len(your_add_names)} adds, net {your_net:+.1f} | "
                    f"Edge: {edge:+.1f}"
                )

        # Aggregate results
        result = BacktestResult(
            league_key=self.league_key,
            team_name=self.team_name,
            start_week=start_week,
            end_week=end_week,
            agent_total_adds=sum(len(wr.agent_adds) for wr in weekly_results),
            your_total_adds=sum(len(wr.actual_adds) for wr in weekly_results),
            agent_total_fpts=sum(wr.agent_net for wr in weekly_results),
            your_total_fpts=sum(wr.your_net for wr in weekly_results),
            total_edge=sum(wr.edge for wr in weekly_results),
            weekly_results=weekly_results,
        )

        print("\n" + result.summary())
        return result


def run_backtest(
    league_key: str = "465.l.17649",
    team_name: str = "McChuckin'",
    start_week: int = 5,
    end_week: int = 20,
) -> BacktestResult:
    """Convenience function to run a backtest."""
    bt = TransactionBacktester(league_key, team_name)
    return bt.run(start_week, end_week)


if __name__ == "__main__":
    import sys

    league = sys.argv[1] if len(sys.argv) > 1 else "465.l.17649"
    team = sys.argv[2] if len(sys.argv) > 2 else "McChuckin'"
    start = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    end = int(sys.argv[4]) if len(sys.argv) > 4 else 15

    print(f"Running backtest: {team} in {league}, weeks {start}-{end}")
    result = run_backtest(league, team, start, end)
    print("\nDone!")

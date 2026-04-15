"""Run the transaction backtest and dump a verbose per-add report.

Uses the existing TransactionBacktester and writes every agent
transaction (and the user's actual transactions) with full context:
player valuations, scoring breakdown, reasoning, and actual FPTS
produced that week.

Output: data/backtest_verbose_report.md
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.core.db import get_session
from src.tools.transactions.backtest import (
    TransactionBacktester,
    _compute_actual_weekly_fpts,
    _get_week_date_range,
    _get_your_transactions,
)
from src.tools.transactions.models import AggressionLevel, PlayerValue


def _fmt_pv(pv: PlayerValue | None, indent: str = "    ") -> str:
    if pv is None:
        return f"{indent}(open slot)"
    lines = [
        f"{indent}{pv.name} ({pv.team}) [{'/'.join(pv.positions)}]",
        f"{indent}  fpts_per_game:    {pv.fpts_per_game:.2f}",
        f"{indent}  fillable/games:   {pv.fillable_games} / {pv.games_in_window}",
        f"{indent}  window_fpts:      {pv.window_fpts:.1f}",
        f"{indent}  ros_value:        {pv.ros_value:.1f}",
        f"{indent}  avg_toi:          {pv.avg_toi:.1f}",
        f"{indent}  games_played:     {pv.games_played}",
        f"{indent}  position_scarcity:{pv.position_scarcity:.2f}",
    ]
    if pv.window_start and pv.window_end:
        lines.append(
            f"{indent}  window:           {pv.window_start}..{pv.window_end} "
            f"({pv.window_days}d)"
        )
    if pv.game_projections:
        gp_str = ", ".join(
            f"{d.strftime('%a %m-%d')}={v:.1f}"
            for d, v in sorted(pv.game_projections.items())
        )
        lines.append(f"{indent}  game_projections: {gp_str}")
    return "\n".join(lines)


def main(
    league_key: str = "465.l.17649",
    team_name: str = "McChuckin'",
    start_week: int = 5,
    end_week: int = 18,
    aggression: AggressionLevel = AggressionLevel.NORMAL,
    out_path: Path = Path("data/backtest_verbose_report.md"),
) -> None:
    bt = TransactionBacktester(
        league_key=league_key,
        team_name=team_name,
        aggression=aggression,
    )
    result = bt.run(start_week=start_week, end_week=end_week)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    lines.append("# Transaction backtest — verbose report\n")
    lines.append(f"- League:     `{league_key}`")
    lines.append(f"- Team:       `{team_name}`")
    lines.append(f"- Weeks:      {start_week}–{end_week}")
    lines.append(f"- Aggression: {aggression.value}")
    lines.append("")
    lines.append("## Totals")
    lines.append("")
    lines.append(
        f"- Agent: **{result.agent_total_adds}** adds → "
        f"**{result.agent_total_fpts:+.1f}** net FPTS"
    )
    lines.append(
        f"- User:  **{result.your_total_adds}** adds → "
        f"**{result.your_total_fpts:+.1f}** net FPTS"
    )
    lines.append(f"- Edge (agent − user): **{result.total_edge:+.1f}**")
    lines.append("")

    with get_session() as session:
        for wr in result.weekly_results:
            lines.append(f"\n---\n\n## Week {wr.yahoo_week}")
            lines.append("")
            lines.append(
                f"Edge **{wr.edge:+.1f}** — agent {wr.agent_net:+.1f} "
                f"({len(wr.agent_adds)} adds), "
                f"you {wr.your_net:+.1f} ({len(wr.actual_adds)} adds)"
            )
            lines.append("")

            # ---- Agent picks ----
            lines.append(f"### Agent transactions ({len(wr.agent_plan.transactions)})")
            lines.append("")
            if not wr.agent_plan.transactions:
                lines.append("_(no adds this week)_")
                lines.append("")
            for i, txn in enumerate(wr.agent_plan.transactions, 1):
                add_actual = _compute_actual_weekly_fpts(
                    session, txn.add_player.nhl_id, wr.yahoo_week
                )
                drop_actual = 0.0
                if txn.drop_player:
                    drop_actual = _compute_actual_weekly_fpts(
                        session, txn.drop_player.nhl_id, wr.yahoo_week
                    )
                net_actual = add_actual - drop_actual

                drop_name = txn.drop_player.name if txn.drop_player else "(open)"
                lines.append(
                    f"#### {i}. ADD {txn.add_player.name} / DROP {drop_name}"
                )
                lines.append("")
                lines.append(
                    f"- projected net weekly: **{txn.net_weekly_fpts:+.1f}**, "
                    f"net ROS: {txn.net_ros_value:+.1f}, "
                    f"score: **{txn.adjusted_score:+.2f}**"
                )
                lines.append(
                    f"- **actual**: add {add_actual:+.1f}, "
                    f"drop {drop_actual:+.1f}, **net {net_actual:+.1f}**"
                )
                lines.append("")
                lines.append("Reasoning:")
                lines.append("")
                for r in txn.reasoning:
                    lines.append(f"- {r}")
                lines.append("")
                lines.append("```")
                lines.append("Add:")
                lines.append(_fmt_pv(txn.add_player))
                lines.append("Drop:")
                lines.append(_fmt_pv(txn.drop_player))
                lines.append("```")
                lines.append("")

            # ---- User's actual transactions ----
            lines.append(f"### User transactions")
            lines.append("")
            week_start, week_end = _get_week_date_range(session, wr.yahoo_week)
            if week_start:
                your_adds, your_drops = _get_your_transactions(
                    session, league_key, team_name, week_start, week_end,
                )
                if not your_adds and not your_drops:
                    lines.append("_(none)_")
                    lines.append("")
                for a in your_adds:
                    actual = (
                        _compute_actual_weekly_fpts(session, a["nhl_id"], wr.yahoo_week)
                        if a["nhl_id"] else 0.0
                    )
                    lines.append(
                        f"- ADD  {a['player_name']} ({a['position']}): "
                        f"actual **{actual:+.1f}**"
                    )
                for d in your_drops:
                    actual = (
                        _compute_actual_weekly_fpts(session, d["nhl_id"], wr.yahoo_week)
                        if d["nhl_id"] else 0.0
                    )
                    lines.append(
                        f"- DROP {d['player_name']} ({d['position']}): "
                        f"actual **{actual:+.1f}**"
                    )
                lines.append("")

    out_path.write_text("\n".join(lines))
    print(f"\nWrote verbose report to {out_path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    kwargs = {}
    if len(args) >= 1:
        kwargs["league_key"] = args[0]
    if len(args) >= 2:
        kwargs["team_name"] = args[1]
    if len(args) >= 3:
        kwargs["start_week"] = int(args[2])
    if len(args) >= 4:
        kwargs["end_week"] = int(args[3])
    main(**kwargs)

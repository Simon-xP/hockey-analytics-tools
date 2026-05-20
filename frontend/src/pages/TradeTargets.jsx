import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import Card from "../components/Card";
import { getRegressionCandidates } from "../api/client";
import "./TradeTargets.css";

function RegressionTable({ players, direction }) {
  const navigate = useNavigate();

  if (!players || players.length === 0) {
    return <p className="empty-state">No data</p>;
  }

  return (
    <table className="regression-table">
      <thead>
        <tr>
          <th>Player</th>
          <th>Pos</th>
          <th>Team</th>
          <th>GP</th>
          <th>G/60</th>
          <th>ixG/60</th>
          <th>Diff</th>
          <th>SH%</th>
          <th>TOI</th>
        </tr>
      </thead>
      <tbody>
        {players.map((p) => (
          <tr
            key={p.nhl_id}
            onClick={() => navigate(`/players/${p.nhl_id}`)}
            className="clickable-row"
          >
            <td className="col-name">{p.name}</td>
            <td>{p.position}</td>
            <td>{p.team}</td>
            <td>{p.gp}</td>
            <td>{p.goals_per_60}</td>
            <td>{p.ixg_per_60}</td>
            <td
              className={
                direction === "buy" ? "stat-negative" : "stat-positive"
              }
            >
              {p.goal_diff > 0 ? "+" : ""}
              {p.goal_diff}
            </td>
            <td>{p.sh_pct ?? "—"}</td>
            <td>{p.avg_toi ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function TradeTargets() {
  const { data, isLoading: loading } = useQuery({
    queryKey: ["regression"],
    queryFn: getRegressionCandidates,
  });

  return (
    <div className="trade-targets">
      <h1>Trade Targets</h1>

      <Card title="Buy Low — Underperforming Expected Goals">
        {loading ? (
          <div className="placeholder-shimmer" />
        ) : (
          <RegressionTable players={data?.buy_low} direction="buy" />
        )}
      </Card>

      <Card title="Sell High — Outperforming Expected Goals">
        {loading ? (
          <div className="placeholder-shimmer" />
        ) : (
          <RegressionTable players={data?.sell_high} direction="sell" />
        )}
      </Card>
    </div>
  );
}

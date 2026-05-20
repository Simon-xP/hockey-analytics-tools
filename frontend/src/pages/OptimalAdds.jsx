import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import Card from "../components/Card";
import { getOptimalAdds, getYahooStatus, getYahooOptimalAdds, getYahooLeagues } from "../api/client";
import "./OptimalAdds.css";

function AddsTable({ players }) {
  const navigate = useNavigate();

  return (
    <table className="adds-table">
      <thead>
        <tr>
          <th className="col-rank">#</th>
          <th>Player</th>
          <th>Pos</th>
          <th>Team</th>
          <th>GP</th>
          <th>TOI</th>
          <th>G</th>
          <th>A</th>
          <th>SOG</th>
          <th>HIT</th>
          <th>BLK</th>
          <th>PIM</th>
          <th className="col-fpts">FPTS/GP</th>
        </tr>
      </thead>
      <tbody>
        {players.map((p, i) => (
          <tr
            key={p.nhl_id || p.name}
            className="clickable-row"
            onClick={() => p.nhl_id && navigate(`/players/${p.nhl_id}`)}
          >
            <td className="col-rank">{i + 1}</td>
            <td className="col-name">
              {p.name}
              {p.status && <span className="player-status">{p.status}</span>}
            </td>
            <td>{p.position}</td>
            <td>{p.team}</td>
            <td>{p.gp}</td>
            <td>{p.avg_toi}</td>
            <td>{p.stats_per_gp?.goals ?? "—"}</td>
            <td>{p.stats_per_gp?.assists ?? "—"}</td>
            <td>{p.stats_per_gp?.shots ?? "—"}</td>
            <td>{p.stats_per_gp?.hits ?? "—"}</td>
            <td>{p.stats_per_gp?.blocks ?? "—"}</td>
            <td>{p.stats_per_gp?.pim ?? "—"}</td>
            <td className="col-fpts">{p.fpts_per_gp}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function YahooAdds() {
  const { data: leagueData, isLoading: leaguesLoading } = useQuery({
    queryKey: ["yahoo-leagues"],
    queryFn: getYahooLeagues,
  });
  const leagueKey = leagueData?.leagues?.[0]?.league_key;

  const { data, isLoading: loading } = useQuery({
    queryKey: ["yahoo-optimal-adds", leagueKey],
    queryFn: () => getYahooOptimalAdds(leagueKey, 50),
    enabled: !!leagueKey,
  });

  if (leaguesLoading || loading) {
    return <div className="placeholder-shimmer" style={{ height: 400 }} />;
  }

  const players = data?.players || [];

  if (players.length === 0) {
    return <p className="empty-state">No free agents with stats found</p>;
  }

  return <AddsTable players={players} />;
}

function AllPlayersAdds() {
  const { data, isLoading: loading } = useQuery({
    queryKey: ["optimal-adds"],
    queryFn: getOptimalAdds,
  });

  if (loading) {
    return <div className="placeholder-shimmer" style={{ height: 400 }} />;
  }

  return <AddsTable players={data?.players || []} />;
}

export default function OptimalAdds() {
  const { data: yahooStatus } = useQuery({
    queryKey: ["yahoo-status"],
    queryFn: getYahooStatus,
  });
  const connected = yahooStatus?.connected;

  return (
    <div className="optimal-adds">
      <h1>Optimal Adds</h1>
      <p className="page-subtitle">
        {connected
          ? "Free agents in your league ranked by fantasy points per game."
          : "All players ranked by fantasy points per game. Connect Yahoo to filter to available free agents."}
      </p>

      <Card>
        {connected ? <YahooAdds /> : <AllPlayersAdds />}
      </Card>
    </div>
  );
}

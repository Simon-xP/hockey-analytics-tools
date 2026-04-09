import { useParams } from "react-router-dom";
import Card from "../components/Card";
import { useApi } from "../hooks/useApi";
import { getPlayer, getPlayerForecast } from "../api/client";
import "./PlayerDetail.css";

function ForecastCard({ nhlId }) {
  const { data, loading } = useApi(() => getPlayerForecast(nhlId), [nhlId]);

  if (loading) return <div className="placeholder-shimmer" style={{ height: 100 }} />;
  if (!data?.predictions || Object.keys(data.predictions).length === 0) {
    return <p className="empty-state">No forecast available</p>;
  }

  const statLabels = {
    goals_per_60: "Goals/60",
    assists_per_60: "Assists/60",
    shots_per_60: "Shots/60",
    hits_per_60: "Hits/60",
    blocked_per_60: "Blocks/60",
  };

  return (
    <div className="forecast-grid">
      {Object.entries(data.predictions).map(([stat, value]) => (
        <div key={stat} className="forecast-stat">
          <span className="forecast-value">{value.toFixed(2)}</span>
          <span className="forecast-label">{statLabels[stat] || stat}</span>
        </div>
      ))}
    </div>
  );
}

export default function PlayerDetail() {
  const { nhlId } = useParams();
  const { data, loading, error } = useApi(() => getPlayer(nhlId), [nhlId]);

  if (loading) return <div className="placeholder-shimmer" style={{ height: 200 }} />;
  if (error) return <p className="error">Failed to load player</p>;
  if (!data?.player) return <p>Player not found</p>;

  const { player, recent_games } = data;

  return (
    <div className="player-detail">
      <div className="player-header">
        <h1>{player.name}</h1>
        <span className="player-meta">{player.position}</span>
      </div>

      <div className="player-grid">
        <Card title="Forecast (XGBoost)" className="span-full">
          <ForecastCard nhlId={nhlId} />
        </Card>

        <Card title="Recent Game Log" className="span-full">
          {recent_games.length === 0 ? (
            <p className="empty-state">No recent games</p>
          ) : (
            <table className="game-log-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Opp</th>
                  <th>TOI</th>
                  <th>G/60</th>
                  <th>A/60</th>
                  <th>S/60</th>
                  <th>ixG/60</th>
                  <th>SH%</th>
                  <th>IPP</th>
                </tr>
              </thead>
              <tbody>
                {recent_games.map((g) => (
                  <tr key={g.date}>
                    <td>{g.date}</td>
                    <td>
                      {g.is_home ? "vs" : "@"} {g.opponent}
                    </td>
                    <td>{g.toi?.toFixed(1)}</td>
                    <td>{g.goals_per_60?.toFixed(2)}</td>
                    <td>{g.assists_per_60?.toFixed(2)}</td>
                    <td>{g.shots_per_60?.toFixed(1)}</td>
                    <td>{g.ixg_per_60?.toFixed(2)}</td>
                    <td>{g.sh_pct?.toFixed(1)}</td>
                    <td>{g.ipp?.toFixed(0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}

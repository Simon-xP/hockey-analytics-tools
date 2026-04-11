import { useNavigate } from "react-router-dom";
import Card from "../components/Card";
import { useApi } from "../hooks/useApi";
import { getGoalieMatchupRankings } from "../api/client";
import "./GoalieMatchups.css";

export default function GoalieMatchups() {
  const { data, loading } = useApi(getGoalieMatchupRankings);
  const navigate = useNavigate();

  const rankings = data?.rankings || [];

  return (
    <div className="goalie-matchups-page">
      <h1>Goalie Streaming Matchups</h1>
      <p className="page-subtitle">
        Teams ranked by how many fantasy points opposing goalies average against
        them. Stream goalies against teams at the top.
      </p>

      <Card>
        {loading ? (
          <div className="placeholder-shimmer" style={{ height: 400 }} />
        ) : (
          <table className="matchups-table">
            <thead>
              <tr>
                <th className="col-rank">#</th>
                <th>Team</th>
                <th className="col-stat">GF/GP</th>
                <th className="col-stat">GA/GP</th>
                <th className="col-stat">Opp W%</th>
                <th className="col-stat">SO%</th>
                <th className="col-fpts">Opp G FPTS</th>
              </tr>
            </thead>
            <tbody>
              {rankings.map((t, i) => (
                <tr key={t.team_id} className="clickable-row">
                  <td className="col-rank">{i + 1}</td>
                  <td className="col-team">
                    <img
                      src={`https://assets.nhle.com/logos/nhl/svg/${t.abbrev}_dark.svg`}
                      alt={t.abbrev}
                      className="matchup-logo"
                    />
                    <span>{t.abbrev}</span>
                  </td>
                  <td className="col-stat">{t.goals_per_game}</td>
                  <td className="col-stat">{t.goals_against_per_game}</td>
                  <td className="col-stat">{t.opp_goalie_win_pct}%</td>
                  <td className="col-stat">{t.shutout_pct}%</td>
                  <td className={`col-fpts ${i < 10 ? "fpts-good" : i >= rankings.length - 10 ? "fpts-bad" : ""}`}>
                    {t.opp_goalie_fpts_avg}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

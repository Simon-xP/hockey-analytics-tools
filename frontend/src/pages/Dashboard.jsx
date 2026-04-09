import { useNavigate } from "react-router-dom";
import Card from "../components/Card";
import { useApi } from "../hooks/useApi";
import {
  getTodayGames,
  getScheduleOutlook,
  getRegressionCandidates,
  getOptimalAdds,
  getYahooStatus,
  getYahooLeagues,
  getYahooOptimalAdds,
} from "../api/client";
import "./Dashboard.css";

function TodayGames() {
  const { data, loading } = useApi(getTodayGames);

  if (loading) return <div className="placeholder-shimmer" />;

  const games = data?.games || [];

  if (games.length === 0) {
    return <p className="empty-state">No games today</p>;
  }

  return (
    <div className="games-list">
      {games.map((g) => (
        <div key={g.game_id} className="game-row">
          <span className="team-abbrev">{g.away_team?.abbrev}</span>
          <span className="at-symbol">@</span>
          <span className="team-abbrev">{g.home_team?.abbrev}</span>
          {g.home_score !== null && (
            <span className="score">
              {g.away_score} - {g.home_score}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

function ScheduleOutlook() {
  const { data, loading } = useApi(() => getScheduleOutlook(7));

  if (loading) return <div className="placeholder-shimmer" />;

  const teams = (data?.teams || []).slice(0, 10);

  return (
    <div className="outlook-list">
      {teams.map((t) => (
        <div key={t.team} className="outlook-row">
          <span className="team-abbrev">{t.team}</span>
          <div className="game-dots">
            {Array.from({ length: t.games }).map((_, i) => (
              <span key={i} className="game-dot" />
            ))}
          </div>
          <span className="game-count">{t.games}GP</span>
        </div>
      ))}
    </div>
  );
}

function RegressionList({ players, direction }) {
  const navigate = useNavigate();

  if (!players || players.length === 0) {
    return <p className="empty-state">No data</p>;
  }

  return (
    <div className="regression-list">
      {players.map((p) => (
        <div
          key={p.nhl_id}
          className="regression-row"
          onClick={() => navigate(`/players/${p.nhl_id}`)}
        >
          <div className="regression-player">
            <span className="player-name">{p.name}</span>
            <span className="player-meta">{p.position} · {p.team}</span>
          </div>
          <div className="regression-stats">
            <div className="stat-pair">
              <span className="stat-label">G/60</span>
              <span className="stat-value">{p.goals_per_60}</span>
            </div>
            <div className="stat-pair">
              <span className="stat-label">ixG/60</span>
              <span className="stat-value">{p.ixg_per_60}</span>
            </div>
            <div className="stat-pair">
              <span className="stat-label">Diff</span>
              <span
                className={`stat-value ${
                  direction === "buy"
                    ? "stat-negative"
                    : "stat-positive"
                }`}
              >
                {p.goal_diff > 0 ? "+" : ""}
                {p.goal_diff}
              </span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function OptimalAddsPreview() {
  const { data: yahooStatus } = useApi(getYahooStatus);
  const { data: leagueData } = useApi(getYahooLeagues);
  const leagueKey = leagueData?.leagues?.[0]?.league_key;
  const connected = yahooStatus?.connected && leagueKey;

  const { data, loading } = useApi(
    () =>
      connected
        ? getYahooOptimalAdds(leagueKey, 8)
        : getOptimalAdds(8),
    [connected, leagueKey]
  );

  const navigate = useNavigate();

  if (loading) return <div className="placeholder-shimmer" />;

  const players = data?.players || [];

  if (players.length === 0) {
    return <p className="empty-state">No data</p>;
  }

  return (
    <div className="adds-preview">
      {players.map((p, i) => (
        <div
          key={p.nhl_id || p.name}
          className="adds-preview-row"
          onClick={() => p.nhl_id && navigate(`/players/${p.nhl_id}`)}
        >
          <span className="adds-rank">{i + 1}</span>
          <div className="adds-player-info">
            <span className="player-name">{p.name}</span>
            <span className="player-meta">{p.position} · {p.team}</span>
          </div>
          <span className="adds-fpts">{p.fpts_per_gp}</span>
        </div>
      ))}
    </div>
  );
}

function BuySellWidgets() {
  const { data, loading } = useApi(getRegressionCandidates);

  if (loading) {
    return (
      <>
        <Card title="Buy Low" linkTo="/trades" className="span-1">
          <div className="placeholder-shimmer" />
        </Card>
        <Card title="Sell High" linkTo="/trades" className="span-1">
          <div className="placeholder-shimmer" />
        </Card>
      </>
    );
  }

  return (
    <>
      <Card title="Buy Low" linkTo="/trades" className="span-1">
        <RegressionList players={data?.buy_low?.slice(0, 5)} direction="buy" />
      </Card>
      <Card title="Sell High" linkTo="/trades" className="span-1">
        <RegressionList
          players={data?.sell_high?.slice(0, 5)}
          direction="sell"
        />
      </Card>
    </>
  );
}

export default function Dashboard() {
  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <h1>Dashboard</h1>
        <span className="date-label">
          {new Date().toLocaleDateString("en-US", {
            weekday: "long",
            month: "long",
            day: "numeric",
          })}
        </span>
      </div>

      <div className="dashboard-grid">
        {/* Row 1: Today's Games + News & Injuries */}
        <Card title="Today's Games" className="span-1">
          <TodayGames />
        </Card>

        <Card title="News & Injuries" className="span-2">
          <p className="empty-state">Injury updates and lineup changes</p>
        </Card>

        {/* Row 2: Roster Projections */}
        <Card title="My Roster Projections" className="span-full">
          <p className="empty-state">
            Connect your Yahoo league to see projections
          </p>
        </Card>

        {/* Row 3: Schedule + Optimal Adds */}
        <Card title="Schedule Outlook (7 days)" className="span-1">
          <ScheduleOutlook />
        </Card>

        <Card title="Optimal Adds" linkTo="/adds" className="span-2">
          <OptimalAddsPreview />
        </Card>

        {/* Row 4: Buy Low + Sell High + League Standings */}
        <BuySellWidgets />

        <Card title="League Standings" className="span-1">
          <p className="empty-state">
            Connect your Yahoo league to see standings
          </p>
        </Card>
      </div>
    </div>
  );
}

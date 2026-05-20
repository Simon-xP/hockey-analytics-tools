import { useRef, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import Card from "../components/Card";
import {
  getTodayGames,
  getScheduleOutlook,
  getRegressionCandidates,
  getOptimalAdds,
  getYahooStatus,
  getYahooLeagues,
  getYahooOptimalAdds,
  getStreamableGoalies,
  getAllInjuries,
  getYahooStandings,
  getYahooRosterWeek,
} from "../api/client";
import "./Dashboard.css";

function formatGameTime(iso) {
  if (!iso) return "TBD";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "TBD";
  return d.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}

function gameStatus(g) {
  const hasScore = g.away_score !== null && g.away_score !== undefined;
  if (!hasScore) return "scheduled";
  if (!g.start_time_utc) return "final";
  const start = new Date(g.start_time_utc);
  const elapsed = (Date.now() - start.getTime()) / 60000;
  if (elapsed > 0 && elapsed < 210) return "live";
  return "final";
}

function GameTile({ game, expanded, onToggle }) {
  const status = gameStatus(game);
  const hasScore = game.away_score !== null && game.away_score !== undefined;

  return (
    <button
      type="button"
      className={`scorebar-game ${expanded ? "expanded" : ""} status-${status}`}
      onClick={onToggle}
    >
      <div className="scorebar-game-header">
        <span className={`scorebar-status status-${status}`}>
          {status === "live" && <span className="status-live-dot" />}
          {status === "live" ? "LIVE" : status === "final" ? "FINAL" : formatGameTime(game.start_time_utc)}
        </span>
      </div>
      <div className="scorebar-game-body">
        <div className="scorebar-team">
          <img
            src={`https://assets.nhle.com/logos/nhl/svg/${game.away}_dark.svg`}
            alt={game.away}
            className="scorebar-logo"
          />
          <div className="scorebar-team-info">
            <span className="scorebar-abbrev">{game.away}</span>
            <span className="scorebar-record">{game.away_record || "—"}</span>
          </div>
          {hasScore && <span className="scorebar-score">{game.away_score}</span>}
        </div>
        <div className="scorebar-team">
          <img
            src={`https://assets.nhle.com/logos/nhl/svg/${game.home}_dark.svg`}
            alt={game.home}
            className="scorebar-logo"
          />
          <div className="scorebar-team-info">
            <span className="scorebar-abbrev">{game.home}</span>
            <span className="scorebar-record">{game.home_record || "—"}</span>
          </div>
          {hasScore && <span className="scorebar-score">{game.home_score}</span>}
        </div>
      </div>
    </button>
  );
}

function GameDetail({ game, onClose }) {
  if (!game) return null;
  const status = gameStatus(game);
  const hasScore = game.away_score !== null && game.away_score !== undefined;

  return (
    <div className="scorebar-detail">
      <div className="scorebar-detail-header">
        <div className="scorebar-detail-status">
          {status === "live" && <span className="status-live-dot" />}
          <span className={`scorebar-detail-tag status-${status}`}>
            {status === "live" ? "IN PROGRESS" : status === "final" ? "FINAL" : "SCHEDULED"}
          </span>
          <span className="scorebar-detail-time">
            {formatGameTime(game.start_time_utc)}
          </span>
        </div>
        <button className="scorebar-detail-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      <div className="scorebar-detail-body">
        <div className="scorebar-detail-team">
          <img
            src={`https://assets.nhle.com/logos/nhl/svg/${game.away}_dark.svg`}
            alt={game.away}
            className="scorebar-detail-logo"
          />
          <div>
            <div className="scorebar-detail-name">{game.away_name || game.away}</div>
            <div className="scorebar-detail-record">{game.away_record || "—"}</div>
          </div>
          {hasScore && <span className="scorebar-detail-score">{game.away_score}</span>}
        </div>
        <div className="scorebar-detail-vs">@</div>
        <div className="scorebar-detail-team">
          <img
            src={`https://assets.nhle.com/logos/nhl/svg/${game.home}_dark.svg`}
            alt={game.home}
            className="scorebar-detail-logo"
          />
          <div>
            <div className="scorebar-detail-name">{game.home_name || game.home}</div>
            <div className="scorebar-detail-record">{game.home_record || "—"}</div>
          </div>
          {hasScore && <span className="scorebar-detail-score">{game.home_score}</span>}
        </div>
      </div>
    </div>
  );
}

function GameBar({ games, label }) {
  const scrollRef = useRef(null);
  const [canScrollRight, setCanScrollRight] = useState(false);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [selectedId, setSelectedId] = useState(null);

  function updateScrollState() {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 0);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
  }

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    updateScrollState();
    el.addEventListener("scroll", updateScrollState);
    window.addEventListener("resize", updateScrollState);
    return () => {
      el.removeEventListener("scroll", updateScrollState);
      window.removeEventListener("resize", updateScrollState);
    };
  }, [games]);

  const selected = games.find((g) => g.game_id === selectedId) || null;

  return (
    <div className="scorebar">
      <div className="scorebar-label">{label}</div>
      <div className="scorebar-track">
        <button
          className={`scorebar-arrow scorebar-arrow-left ${canScrollLeft ? "arrow-visible" : ""}`}
          onClick={() => scrollRef.current?.scrollBy({ left: -240, behavior: "smooth" })}
        >
          ‹
        </button>
        <div className="scorebar-inner" ref={scrollRef}>
          {games.map((g, i) => (
            <GameTile
              key={g.game_id || i}
              game={g}
              expanded={g.game_id === selectedId}
              onToggle={() =>
                setSelectedId(g.game_id === selectedId ? null : g.game_id)
              }
            />
          ))}
        </div>
        <button
          className={`scorebar-arrow scorebar-arrow-right ${canScrollRight ? "arrow-visible" : ""}`}
          onClick={() => scrollRef.current?.scrollBy({ left: 240, behavior: "smooth" })}
        >
          ›
        </button>
      </div>
      {selected && <GameDetail game={selected} onClose={() => setSelectedId(null)} />}
    </div>
  );
}

function Scorebar() {
  const { data, isLoading: loading } = useQuery({ queryKey: ["today-games"], queryFn: getTodayGames });

  if (loading) {
    return (
      <div className="scorebar">
        <div className="scorebar-inner">
          <div className="placeholder-shimmer" style={{ height: 92, width: "100%" }} />
        </div>
      </div>
    );
  }

  const rawGames = data?.games || [];
  const games = rawGames.map((g) => ({
    game_id: g.game_id,
    away: g.away_team?.abbrev,
    home: g.home_team?.abbrev,
    away_name: g.away_team?.name,
    home_name: g.home_team?.name,
    away_record: g.away_team?.record,
    home_record: g.home_team?.record,
    away_score: g.away_score,
    home_score: g.home_score,
    start_time_utc: g.start_time_utc,
  }));

  const label = games.length > 0 ? `${games.length} Games Today` : "No Games Today";

  return <GameBar games={games} label={label} />;
}

function ScheduleOutlook() {
  const { data, isLoading: loading } = useQuery({ queryKey: ["schedule-outlook"], queryFn: getScheduleOutlook });

  if (loading) return <div className="placeholder-shimmer" />;

  const teams = (data?.teams || []).slice(0, 6);
  const dayLabels = data?.day_labels || ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  return (
    <div className="outlook-list">
      <div className="outlook-header">
        <span className="outlook-team-col"></span>
        {dayLabels.map((d, i) => (
          <span key={i} className="outlook-day-label">{d}</span>
        ))}
        <span className="outlook-total-label">GP</span>
      </div>
      {teams.map((t) => (
        <div key={t.team} className="outlook-row">
          <span className="outlook-team-col">
            <img
              src={`https://assets.nhle.com/logos/nhl/svg/${t.team}_dark.svg`}
              alt={t.team}
              className="outlook-logo"
            />
            <span className="team-abbrev">{t.team}</span>
          </span>
          {(t.days || []).map((hasGame, i) => (
            <span key={i} className={`outlook-day ${hasGame ? "outlook-day-active" : ""}`}>
              {hasGame ? "●" : "—"}
            </span>
          ))}
          <span className="game-count">{t.games}</span>
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
  const { data: yahooStatus } = useQuery({ queryKey: ["yahoo-status"], queryFn: getYahooStatus });
  const { data: leagueData } = useQuery({ queryKey: ["yahoo-leagues"], queryFn: getYahooLeagues, enabled: !!yahooStatus?.connected });
  const leagueKey = leagueData?.leagues?.[0]?.league_key;
  const connected = yahooStatus?.connected && leagueKey;

  const { data, isLoading: loading } = useQuery({
    queryKey: ["optimal-adds-preview", connected ? "yahoo" : "generic", leagueKey],
    queryFn: () => connected ? getYahooOptimalAdds(leagueKey, 4) : getOptimalAdds(4),
    enabled: yahooStatus !== undefined,
  });

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
  const { data, isLoading: loading } = useQuery({ queryKey: ["regression"], queryFn: getRegressionCandidates });

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

// Map Daily Faceoff team slugs to NHL abbreviations for logos
const SLUG_TO_ABBREV = {
  "anaheim-ducks": "ANA", "utah-hockey-club": "UTA", "boston-bruins": "BOS",
  "buffalo-sabres": "BUF", "calgary-flames": "CGY", "carolina-hurricanes": "CAR",
  "chicago-blackhawks": "CHI", "colorado-avalanche": "COL",
  "columbus-blue-jackets": "CBJ", "dallas-stars": "DAL",
  "detroit-red-wings": "DET", "edmonton-oilers": "EDM",
  "florida-panthers": "FLA", "los-angeles-kings": "LAK",
  "minnesota-wild": "MIN", "montreal-canadiens": "MTL",
  "nashville-predators": "NSH", "new-jersey-devils": "NJD",
  "new-york-islanders": "NYI", "new-york-rangers": "NYR",
  "ottawa-senators": "OTT", "philadelphia-flyers": "PHI",
  "pittsburgh-penguins": "PIT", "san-jose-sharks": "SJS",
  "seattle-kraken": "SEA", "st-louis-blues": "STL",
  "tampa-bay-lightning": "TBL", "toronto-maple-leafs": "TOR",
  "vancouver-canucks": "VAN", "vegas-golden-knights": "VGK",
  "washington-capitals": "WSH", "winnipeg-jets": "WPG",
};

function StreamableGoaliesWidget() {
  const navigate = useNavigate();
  const { data, isLoading: loading } = useQuery({ queryKey: ["streamable-goalies"], queryFn: getStreamableGoalies });

  if (loading) return <div className="placeholder-shimmer" />;

  const goalies = data?.goalies || [];
  if (goalies.length === 0) {
    return <p className="empty-state">No streamable goalies available</p>;
  }

  // NHL team primary colors for tile backgrounds
  const TEAM_COLORS = {
    ANA: "#F47A38", UTA: "#69B3E7", BOS: "#FFB81C", BUF: "#002654",
    CGY: "#D2001C", CAR: "#CC0000", CHI: "#CF0A2C", COL: "#6F263D",
    CBJ: "#002654", DAL: "#006847", DET: "#CE1126", EDM: "#041E42",
    FLA: "#041E42", LAK: "#A2AAAD", MIN: "#154734", MTL: "#AF1E2D",
    NSH: "#FFB81C", NJD: "#CE1126", NYI: "#00539B", NYR: "#0038A8",
    OTT: "#C52032", PHI: "#F74902", PIT: "#FCB514", SJS: "#006D75",
    SEA: "#99D9D9", STL: "#002F87", TBL: "#002868", TOR: "#00205B",
    VAN: "#00205B", VGK: "#B4975A", WSH: "#C8102E", WPG: "#041E42",
  };

  return (
    <div className="streamable-goalies-list">
      {goalies.slice(0, 3).map((g, i) => {
        const teamAbbrev = SLUG_TO_ABBREV[g.team_slug] || "";
        const oppAbbrev = SLUG_TO_ABBREV[g.opponent_slug] || "";
        const teamColor = TEAM_COLORS[teamAbbrev] || "#1a1a3a";

        return (
          <div
            key={i}
            className="streamable-goalie-tile"
            style={{ borderLeft: `3px solid ${teamColor}` }}
            onClick={(e) => { e.stopPropagation(); g.nhl_id && navigate(`/players/${g.nhl_id}`); }}
          >
            <div className="sg-left">
              <div className="sg-team-badge" style={{ background: teamColor + "30" }}>
                <img
                  src={`https://assets.nhle.com/logos/nhl/svg/${teamAbbrev}_dark.svg`}
                  alt={teamAbbrev}
                  className="sg-logo"
                />
              </div>
              <div className="sg-info">
                <span className="sg-name">{g.name}</span>
                <span className={`goalie-confirm ${
                  g.confirmation === "Confirmed" ? "confirmed" :
                  g.confirmation ? "unconfirmed" : "unknown"
                }`}>
                  {g.confirmation || "Unannounced"}
                </span>
                {(g.sv_pct && g.sv_pct !== "0.0") || (g.gaa && g.gaa !== "0.0") ? (
                  <div className="sg-stats-row">
                    {g.sv_pct && g.sv_pct !== "0.0" && (
                      <span className="sg-stat">{g.sv_pct} SV%</span>
                    )}
                    {g.gaa && g.gaa !== "0.0" && (
                      <span className="sg-stat">{g.gaa} GAA</span>
                    )}
                  </div>
                ) : null}
              </div>
            </div>
            <div className="sg-matchup-badge">
              <span className="sg-matchup-label">{g.is_home ? "vs" : "@"}</span>
              <img
                src={`https://assets.nhle.com/logos/nhl/svg/${oppAbbrev}_dark.svg`}
                alt={oppAbbrev}
                className="sg-opp-logo"
              />
              <span className="sg-opp-abbrev">{oppAbbrev}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

const SEVERITY_COLOR = {
  season: "#ef4444",
  "month-plus": "#f97316",
  "week-to-week": "#eab308",
  "day-to-day": "#7c5cfc",
  unknown: "#94a3b8",
};

const SEVERITY_LABEL = {
  season: "SEASON",
  "month-plus": "MONTH+",
  "week-to-week": "WTW",
  "day-to-day": "DTD",
  unknown: "UNK",
};

function InjuryWidget() {
  const { data, isLoading: loading } = useQuery({ queryKey: ["injuries"], queryFn: getAllInjuries });
  const navigate = useNavigate();

  if (loading) return <div className="placeholder-shimmer" />;

  const items = (data?.items || []).slice(0, 8);

  if (items.length === 0) {
    return <p className="empty-state">No injuries on file</p>;
  }

  return (
    <div className="news-feed-list">
      {items.map((inj, i) => {
        const sevColor = SEVERITY_COLOR[inj.severity] || SEVERITY_COLOR.unknown;
        const clickable = !!inj.nhl_id;
        return (
          <div
            key={i}
            className={`news-card ${clickable ? "news-card-clickable" : ""}`}
            onClick={() => clickable && navigate(`/players/${inj.nhl_id}`)}
          >
            <div className="news-card-left">
              {inj.headshot ? (
                <img
                  src={inj.headshot}
                  alt=""
                  className="news-headshot"
                  onError={(e) => {
                    if (inj.team_abbrev) {
                      e.target.src = `https://assets.nhle.com/logos/nhl/svg/${inj.team_abbrev}_dark.svg`;
                      e.target.className = "news-team-logo-fallback";
                    } else {
                      e.target.style.display = "none";
                    }
                  }}
                />
              ) : inj.team_abbrev ? (
                <img
                  src={`https://assets.nhle.com/logos/nhl/svg/${inj.team_abbrev}_dark.svg`}
                  alt=""
                  className="news-team-logo-fallback"
                />
              ) : null}
            </div>
            <div className="news-card-content">
              <div className="news-summary-row">
                <span
                  className="news-category-badge"
                  style={{ background: sevColor + "20", color: sevColor }}
                >
                  {SEVERITY_LABEL[inj.severity] || "UNK"}
                </span>
                <p className="news-summary">
                  {inj.player_name}
                  {inj.body_part ? ` — ${inj.body_part}` : ""}
                </p>
              </div>
              <p className="news-detail">{inj.summary}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function LeagueStandingsWidget() {
  const { data: yahooStatus } = useQuery({ queryKey: ["yahoo-status"], queryFn: getYahooStatus });
  const { data: leagueData } = useQuery({ queryKey: ["yahoo-leagues"], queryFn: getYahooLeagues, enabled: !!yahooStatus?.connected });
  const leagueKey = leagueData?.leagues?.[0]?.league_key;
  const connected = yahooStatus?.connected && leagueKey;

  const { data, isLoading: loading } = useQuery({
    queryKey: ["yahoo-standings", leagueKey],
    queryFn: () => getYahooStandings(leagueKey),
    enabled: !!connected,
  });

  if (!connected) {
    return <p className="empty-state">Connect Yahoo to see league standings</p>;
  }
  if (loading) return <div className="placeholder-shimmer" />;

  const standings = data?.standings || [];

  return (
    <div className="standings-list">
      {standings.map((t) => (
        <div key={t.team_key} className="standings-row">
          <span className="standings-rank">{t.rank}</span>
          <span className="standings-name">{t.name}</span>
          <span className="standings-record">{t.wins}-{t.losses}-{t.ties || 0}</span>
        </div>
      ))}
    </div>
  );
}

function RosterProjectionsWidget() {
  const navigate = useNavigate();
  const { data: yahooStatus } = useQuery({ queryKey: ["yahoo-status"], queryFn: getYahooStatus });
  const { data: leagueData } = useQuery({ queryKey: ["yahoo-leagues"], queryFn: getYahooLeagues, enabled: !!yahooStatus?.connected });
  const leagueKey = leagueData?.leagues?.[0]?.league_key;
  const connected = yahooStatus?.connected && leagueKey;

  const { data, isLoading: loading } = useQuery({
    queryKey: ["yahoo-roster-week", leagueKey],
    queryFn: () => getYahooRosterWeek(leagueKey),
    enabled: !!connected,
  });

  if (!connected) {
    return <p className="empty-state">Connect Yahoo to see roster projections</p>;
  }
  if (loading) return <div className="placeholder-shimmer" />;
  if (!data?.roster) return <p className="empty-state">No roster data</p>;

  // Show active skaters sorted by FPTS/GP, top 6
  const active = data.roster
    .filter((p) => !["BN", "IR", "IR+", "NA", "G"].includes(p.selected_position))
    .filter((p) => p.fpts_per_gp)
    .sort((a, b) => b.fpts_per_gp - a.fpts_per_gp)
    .slice(0, 6);

  return (
    <div className="projections-list">
      {active.map((p) => (
        <div
          key={p.name}
          className="projection-row clickable-row"
          onClick={() => p.nhl_id && navigate(`/players/${p.nhl_id}`)}
        >
          <div className="projection-player">
            <span className="projection-name">{p.name}</span>
            <span className="projection-meta">{p.position} · {p.team}</span>
          </div>
          <div className="projection-stats">
            <div className="projection-stat">
              <span className="projection-value">{p.games_this_week}</span>
              <span className="projection-label">GP</span>
            </div>
            <div className="projection-stat">
              <span className="projection-value projection-fpts">{p.fpts_per_gp}</span>
              <span className="projection-label">FPTS/GP</span>
            </div>
            <div className="projection-stat">
              <span className="projection-value projection-fpts">
                {(p.fpts_per_gp * p.games_this_week).toFixed(1)}
              </span>
              <span className="projection-label">Wk Total</span>
            </div>
          </div>
        </div>
      ))}
    </div>
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

      <Scorebar />

      <div className="dashboard-grid">
        {/* Row 1: Streamable Goalies + News */}
        <Card title="Streamable Goalies" linkTo="/streamable-goalies" className="span-1">
          <StreamableGoaliesWidget />
        </Card>

        <Card title="Injuries" linkTo="/injuries" className="span-2 card-fixed">
          <InjuryWidget />
        </Card>

        {/* Row 2: Roster Projections + Schedule + Optimal Adds */}
        <Card title="My Roster" linkTo="/roster" className="span-1 card-fixed">
          <RosterProjectionsWidget />
        </Card>

        <Card title="Schedule Outlook" className="span-1">
          <ScheduleOutlook />
        </Card>

        <Card title="Optimal Adds" linkTo="/adds" className="span-1 card-fixed">
          <OptimalAddsPreview />
        </Card>

        {/* Row 3: Buy Low + Sell High + League Standings */}
        <BuySellWidgets />

        <Card title="League Standings" className="span-1 card-fixed">
          <LeagueStandingsWidget />
        </Card>
      </div>
    </div>
  );
}

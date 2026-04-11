import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import Card from "../components/Card";
import { useApi } from "../hooks/useApi";
import { getStreamableGoalies } from "../api/client";
import "./StreamableGoalies.css";

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

function Tooltip({ text }) {
  const [show, setShow] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const triggerRef = useRef(null);

  function handleEnter() {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) {
      setPos({
        top: rect.top - 8,
        left: Math.min(rect.left, window.innerWidth - 280),
      });
    }
    setShow(true);
  }

  return (
    <span className="tooltip-wrapper" ref={triggerRef}>
      <span
        className="tooltip-trigger"
        onMouseEnter={handleEnter}
        onMouseLeave={() => setShow(false)}
      >
        ?
      </span>
      {show && (
        <span
          className="tooltip-bubble"
          style={{ top: pos.top, left: pos.left, transform: "translateY(-100%)" }}
        >
          {text}
        </span>
      )}
    </span>
  );
}

export default function StreamableGoalies() {
  const { data, loading } = useApi(getStreamableGoalies);
  const navigate = useNavigate();

  const goalies = data?.goalies || [];

  return (
    <div className="streamable-page">
      <h1>Streamable Goalies</h1>
      <p className="page-subtitle">
        Unrostered goalies ranked by matchup quality.
      </p>

      <Card>
        {loading ? (
          <div className="placeholder-shimmer" style={{ height: 400 }} />
        ) : goalies.length === 0 ? (
          <p className="empty-state">No streamable goalies available</p>
        ) : (
          <div className="sg-list">
            {/* Section headers */}
            <div className="sg-section-labels">
              <span className="sg-section-label sg-section-pick">Pickup</span>
              <span className="sg-section-label sg-section-goalie">Goalie Stats</span>
              <span className="sg-section-label sg-section-opp">Opponent Stats</span>
            </div>

            {/* Column headers */}
            <div className="sg-header-row">
              <span className="sg-col-name">Goalie</span>
              <span className="sg-col-score">
                Score
                <Tooltip text="Goalie quality + opponent softness combined. Higher = better pickup." />
              </span>
              <span className="sg-col-divider" />
              <span className="sg-col-stat">SV%</span>
              <span className="sg-col-stat">GAA</span>
              <span className="sg-col-stat">W%</span>
              <span className="sg-col-divider" />
              <span className="sg-col-opp">Opponent</span>
              <span className="sg-col-stat">GF/GP</span>
              <span className="sg-col-stat">
                Softness
                <Tooltip text="Avg fantasy points opposing goalies earn vs this team. Higher = softer. Based on goals allowed, saves, wins, shutouts." />
              </span>
            </div>

            {goalies.map((g, i) => {
              const teamAbbrev = SLUG_TO_ABBREV[g.team_slug] || "";
              const oppAbbrev = SLUG_TO_ABBREV[g.opponent_slug] || g.opponent_abbrev || "";
              const winPct = g.wins && g.losses
                ? ((g.wins / (g.wins + g.losses)) * 100).toFixed(0)
                : "—";

              // Combine goalie quality + opponent softness for a pick score
              const svPctNum = g.sv_pct && g.sv_pct !== "0.0" ? parseFloat(g.sv_pct) : null;
              const gaaNum = g.gaa && g.gaa !== "0.0" ? parseFloat(g.gaa) : null;
              const goalieScore = svPctNum ? (svPctNum - 0.880) * 100 : 0; // normalize around league avg
              const pickScore = (g.stream_score + goalieScore).toFixed(1);

              return (
                <div
                  key={i}
                  className={`sg-row ${g.nhl_id ? "sg-row-clickable" : ""}`}
                  onClick={() => g.nhl_id && navigate(`/players/${g.nhl_id}`)}
                >
                  <div className="sg-col-name">
                    <span className={`confirm-dot ${
                      g.confirmation === "Confirmed" ? "dot-confirmed" :
                      g.confirmation ? "dot-unconfirmed" : "dot-unknown"
                    }`} />
                    <img
                      src={`https://assets.nhle.com/logos/nhl/svg/${teamAbbrev}_dark.svg`}
                      alt={teamAbbrev}
                      className="sg-tbl-logo"
                    />
                    <div className="sg-name-block">
                      <span className="sg-player-name">{g.name}</span>
                      <span className="sg-team-label">{teamAbbrev}</span>
                    </div>
                  </div>
                  <span className="sg-col-score">{pickScore}</span>
                  <span className="sg-col-divider" />
                  <span className="sg-col-stat">{svPctNum ? g.sv_pct : "—"}</span>
                  <span className="sg-col-stat">{gaaNum ? g.gaa : "—"}</span>
                  <span className="sg-col-stat">{winPct}%</span>
                  <span className="sg-col-divider" />
                  <div className="sg-col-opp">
                    <span className="sg-home-away">{g.is_home ? "vs" : "@"}</span>
                    <img
                      src={`https://assets.nhle.com/logos/nhl/svg/${oppAbbrev}_dark.svg`}
                      alt={oppAbbrev}
                      className="sg-tbl-logo"
                    />
                    <span className="sg-opp-label">{oppAbbrev}</span>
                  </div>
                  <span className="sg-col-stat">{g.opp_goals_per_game ?? "—"}</span>
                  <span className="sg-col-stat sg-softness">{g.stream_score}</span>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

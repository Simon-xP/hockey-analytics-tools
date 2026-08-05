// Fleet overview: every league the agent is running, at a glance.
// Data is mock (src/mock/agentData.js) until the agent loop exists.
import { useNavigate } from "react-router-dom";
import { useAgentStore } from "../state/agentContext";
import { ControlButtons, ModeTag, Scoreline, StatusPill, WinProbBar } from "../components/AgentControls";
import "./Fleet.css";

function nextMove(league) {
  return league.plan.moves.find((m) => m.status !== "DONE" && m.status !== "SKIPPED");
}

function FleetSummary({ leagues }) {
  const active = leagues.filter((l) => l.status === "ACTIVE").length;
  const paused = leagues.filter((l) => l.status === "PAUSED").length;
  const stopped = leagues.filter((l) => l.status === "STOPPED").length;
  const leading = leagues.filter((l) => l.matchup.myProjected > l.matchup.oppProjected).length;
  const pending = leagues.reduce(
    (n, l) => n + l.plan.moves.filter((m) => m.status === "AWAITING_APPROVAL").length,
    0
  );
  const movesUsed = leagues.reduce((n, l) => n + l.movesUsed, 0);

  const cells = [
    { label: "Leagues", value: leagues.length, sub: `${active} active · ${paused} paused · ${stopped} stopped` },
    { label: "Projected to win", value: `${leading}/${leagues.length}`, sub: "this week's matchups" },
    { label: "Awaiting approval", value: pending, sub: pending ? "review-mode leagues" : "nothing blocked" },
    { label: "Moves this week", value: movesUsed, sub: "across the fleet" },
  ];

  return (
    <div className="fleet-summary">
      {cells.map((c) => (
        <div key={c.label} className="fleet-summary-cell">
          <span className="fleet-summary-label">{c.label}</span>
          <span className="fleet-summary-value">{c.value}</span>
          <span className="fleet-summary-sub">{c.sub}</span>
        </div>
      ))}
    </div>
  );
}

function LeagueCard({ league }) {
  const navigate = useNavigate();
  const move = nextMove(league);
  const stale = league.status !== "ACTIVE";

  return (
    <div
      className={`fleet-card fleet-card-${league.status.toLowerCase()}`}
      onClick={(e) => {
        if (e.target.closest("button")) return;
        navigate(`/league/${league.id}`);
      }}
    >
      <div className="fleet-card-top">
        <div className="fleet-card-ident">
          <span className="fleet-league">{league.league}</span>
          <span className="fleet-team">
            {league.team} · {league.record} · #{league.rank}/{league.size}
          </span>
        </div>
        <StatusPill status={league.status} size="sm" />
      </div>

      <div className="fleet-card-score">
        <Scoreline matchup={league.matchup} teamName={league.team} compact />
        <WinProbBar winProb={league.matchup.winProb} />
      </div>

      <div className="fleet-card-plan">
        {stale ? (
          <div className="fleet-plan-halt">
            {league.status === "PAUSED" ? "Plan frozen" : "Execution halted"}
            <span className="fleet-plan-halt-note">
              {league.pauseNote || league.stoppedReason}
            </span>
          </div>
        ) : move ? (
          <>
            <span className="fleet-plan-label">Next planned move</span>
            <div className="fleet-plan-move">
              <span className="fleet-fire-day">{move.fireDay}</span>
              <span className="fleet-move-text">
                <span className="move-add">+{move.add.name}</span>
                <span className="move-drop">-{move.drop.name}</span>
              </span>
              <span className="fleet-move-gain">+{move.gain.toFixed(1)}</span>
            </div>
          </>
        ) : (
          <div className="fleet-plan-idle">No moves planned</div>
        )}
      </div>

      <div className="fleet-card-foot">
        <div className="fleet-foot-meta">
          <ModeTag mode={league.mode} />
          <span className="fleet-meta-item">{league.aggression}</span>
          <span className="fleet-meta-item">
            {league.movesUsed}/{league.moveCap} moves
          </span>
          <span className="fleet-meta-item fleet-meta-dim">ran {league.lastRun}</span>
        </div>
        <ControlButtons league={league} compact />
      </div>
    </div>
  );
}

export default function Fleet() {
  const { leagues, pauseAll, resumeAll } = useAgentStore();

  return (
    <div className="fleet-page">
      <div className="fleet-header">
        <div>
          <h1>
            Fleet <span className="agent-mock-tag">MOCK DATA</span>
          </h1>
          <p className="page-subtitle">Every league the agent is running.</p>
        </div>
        <div className="agent-controls">
          <button className="agent-btn" onClick={pauseAll}>
            Pause all
          </button>
          <button className="agent-btn agent-btn-go" onClick={resumeAll}>
            Resume all
          </button>
        </div>
      </div>

      <FleetSummary leagues={leagues} />

      <div className="fleet-grid">
        {leagues.map((l) => (
          <LeagueCard key={l.id} league={l} />
        ))}
      </div>
    </div>
  );
}

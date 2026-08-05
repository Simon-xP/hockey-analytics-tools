// Shared agent widgets used by both the fleet overview and the per-league monitor.
import { useAgentStore } from "../state/agentContext";
import "./AgentControls.css";

const STATUS_LABEL = {
  ACTIVE: "Active",
  PAUSED: "Paused",
  STOPPED: "Stopped",
};

export function StatusPill({ status, size = "md" }) {
  const cls = status.toLowerCase();
  return (
    <span className={`status-pill pill-${cls} pill-${size}`}>
      <span className={`pill-dot dot-${cls}`} />
      {STATUS_LABEL[status] || status}
    </span>
  );
}

export function ModeTag({ mode }) {
  return (
    <span className={`mode-tag mode-${mode.toLowerCase()}`}>
      {mode === "AUTO" ? "Auto-execute" : "Review first"}
    </span>
  );
}

export function ControlButtons({ league, compact = false }) {
  const { pause, resume, stop } = useAgentStore();
  const running = league.status === "ACTIVE";

  return (
    <div className={`agent-controls ${compact ? "compact" : ""}`}>
      {running ? (
        <button className="agent-btn" onClick={() => pause(league.id)}>
          Pause
        </button>
      ) : (
        <button className="agent-btn agent-btn-go" onClick={() => resume(league.id)}>
          {league.status === "STOPPED" ? "Restart" : "Resume"}
        </button>
      )}
      <button
        className="agent-btn agent-btn-kill"
        disabled={league.status === "STOPPED"}
        onClick={() => stop(league.id)}
      >
        Kill
      </button>
    </div>
  );
}

export function WinProbBar({ winProb, showLabel = true }) {
  const pct = Math.round(winProb * 100);
  const tone = pct >= 60 ? "good" : pct >= 40 ? "even" : "bad";
  return (
    <div className="winprob">
      <div className="winprob-track">
        <div className={`winprob-fill fill-${tone}`} style={{ width: `${pct}%` }} />
      </div>
      {showLabel && <span className={`winprob-label label-${tone}`}>{pct}% win</span>}
    </div>
  );
}

export function Scoreline({ matchup, teamName, compact = false }) {
  const leading = matchup.myScore >= matchup.oppScore;
  const projLeading = matchup.myProjected >= matchup.oppProjected;

  return (
    <div className={`scoreline ${compact ? "compact" : ""}`}>
      <div className="score-side">
        <span className="score-team">{teamName}</span>
        <span className={`score-value ${leading ? "score-lead" : ""}`}>{matchup.myScore.toFixed(1)}</span>
        {!compact && (
          <span className={`score-proj ${projLeading ? "proj-lead" : ""}`}>
            proj {matchup.myProjected.toFixed(0)}
          </span>
        )}
      </div>
      <span className="score-sep">vs</span>
      <div className="score-side score-side-right">
        <span className="score-team">{matchup.opponent}</span>
        <span className={`score-value ${!leading ? "score-lead" : ""}`}>{matchup.oppScore.toFixed(1)}</span>
        {!compact && (
          <span className={`score-proj ${!projLeading ? "proj-lead" : ""}`}>
            proj {matchup.oppProjected.toFixed(0)}
          </span>
        )}
      </div>
    </div>
  );
}

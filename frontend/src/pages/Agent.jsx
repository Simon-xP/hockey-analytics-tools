// Per-league agent monitor.
//
// MOCK DATA. Everything renders out of src/mock/agentData.js via the in-memory
// store in src/state/agentStore.jsx. The controls (pause, kill, approve, hold,
// protect, execution mode) mutate that store only; nothing reaches Yahoo.
// See docs/autonomous-agent.md for the wiring plan.
import { useState } from "react";
import { useParams } from "react-router-dom";
import Card from "../components/Card";
import { ControlButtons, Scoreline, StatusPill, WinProbBar } from "../components/AgentControls";
import { useAgentStore } from "../state/agentContext";
import { planValue, TRIGGER_META } from "../mock/agentData";
import "./Agent.css";

const TABS = [
  { id: "plan", label: "Week plan" },
  { id: "team", label: "My team" },
  { id: "activity", label: "Activity" },
  { id: "pulse", label: "Manager moves" },
  { id: "watchlist", label: "Watchlist" },
];

const MOVE_STATUS_META = {
  SCHEDULED: { label: "Scheduled", cls: "move-scheduled" },
  AWAITING_CONFIRM: { label: "Awaiting confirm", cls: "move-waiting" },
  AWAITING_APPROVAL: { label: "Needs your approval", cls: "move-approval" },
  HELD: { label: "Held", cls: "move-held" },
  FROZEN: { label: "Frozen", cls: "move-frozen" },
  DONE: { label: "Executed", cls: "move-done" },
  SKIPPED: { label: "Cancelled", cls: "move-skipped" },
};

/* ========================= Header + control bar ========================= */

// League switching lives in the sidebar, so the header is identity only.
function Header({ league }) {
  return (
    <div className="agent-header">
      <div>
        <h1>
          {league.league} <span className="agent-mock-tag">MOCK DATA</span>
        </h1>
        <p className="page-subtitle">
          {league.team} · {league.record} · #{league.rank} of {league.size} · {league.week.label} (
          {league.week.start} – {league.week.end})
        </p>
      </div>
    </div>
  );
}

function ControlBar({ league }) {
  const { setMode } = useAgentStore();
  const halted = league.status !== "ACTIVE";

  return (
    <div className="control-bar">
      <div className="control-group">
        <StatusPill status={league.status} />
        <span className="control-runs">
          ran {league.lastRun} · next {league.nextRun}
        </span>
      </div>

      <div className="control-group control-readouts">
        {/* Aggression and the move cap are model/config outputs, so read-only here. */}
        <div className="readout">
          <span className="control-label">Aggression</span>
          <span className="readout-value">{league.aggression}</span>
          <span className="readout-sub" title={league.aggressionReason}>
            {league.aggressionReason}
          </span>
        </div>

        <div className="readout">
          <span className="control-label">Move cap</span>
          <span className="readout-value">
            {league.movesUsed}/{league.moveCap}
          </span>
          <span className="readout-sub">used this week</span>
        </div>

        <label className="control-field">
          <span className="control-label">Execution</span>
          <select
            className="control-select"
            value={league.mode}
            onChange={(e) => setMode(league.id, e.target.value)}
          >
            <option value="AUTO">Auto-execute</option>
            <option value="REVIEW">Review first</option>
          </select>
        </label>
      </div>

      <ControlButtons league={league} />

      {halted && (
        <div className={`control-banner banner-${league.status.toLowerCase()}`}>
          <strong>{league.status === "PAUSED" ? "Paused" : "Stopped"}</strong>
          <span>
            {league.status === "PAUSED"
              ? `${league.pauseNote || "Transactions suspended."} (${league.pausedBy || "you"}, ${league.pausedAt})`
              : `${league.stoppedReason} (${league.stoppedAt})`}
          </span>
        </div>
      )}
    </div>
  );
}

/* ============================ Matchup banner ============================ */

function MatchupBanner({ league }) {
  const m = league.matchup;
  const maxDay = Math.max(...m.days.flatMap((d) => [d.mine, d.theirs]));

  return (
    <div className="matchup-banner">
      <div className="matchup-main">
        <Scoreline matchup={m} teamName={league.team} />
        <WinProbBar winProb={m.winProb} />
        <div className="matchup-meta">
          <span>
            <strong>{m.myGamesLeft}</strong> games left
          </span>
          <span className="matchup-meta-sep" />
          <span>
            opponent <strong>{m.oppGamesLeft}</strong>
          </span>
          <span className="matchup-meta-sep" />
          <span>
            projected margin{" "}
            <strong className={m.myProjected >= m.oppProjected ? "pos" : "neg"}>
              {m.myProjected >= m.oppProjected ? "+" : ""}
              {(m.myProjected - m.oppProjected).toFixed(1)}
            </strong>
          </span>
        </div>
      </div>

      <div className="matchup-days">
        {m.days.map((d) => (
          <div key={d.day} className={`matchup-day ${d.played ? "day-played" : "day-future"}`}>
            <div className="day-bars">
              <div
                className="day-bar day-bar-mine"
                style={{ height: `${Math.max(4, (d.mine / maxDay) * 100)}%` }}
                title={`${league.team}: ${d.mine.toFixed(1)}`}
              />
              <div
                className="day-bar day-bar-theirs"
                style={{ height: `${Math.max(4, (d.theirs / maxDay) * 100)}%` }}
                title={`${m.opponent}: ${d.theirs.toFixed(1)}`}
              />
            </div>
            <span className="day-name">{d.day}</span>
          </div>
        ))}
        <div className="matchup-days-legend">
          <span className="legend-mine">you</span>
          <span className="legend-theirs">opp</span>
        </div>
      </div>
    </div>
  );
}

/* =============================== Week plan =============================== */

function PlanSummary({ league }) {
  const p = league.plan;
  const delta = planValue(p);
  const pending = p.moves.filter((m) => !["DONE", "SKIPPED"].includes(m.status)).length;
  const wp = league.matchup;

  const cells = [
    { label: "Projected with plan", value: (p.baselineTotal + delta).toFixed(1), tone: "pos" },
    { label: "If the agent does nothing", value: p.baselineTotal.toFixed(1) },
    {
      label: "Plan value",
      value: `${delta >= 0 ? "+" : ""}${delta.toFixed(1)}`,
      tone: delta >= 0 ? "pos" : "neg",
      sub: `built ${p.generatedAt}`,
    },
    {
      label: "Win prob with plan",
      value: `${Math.round(wp.winProb * 100)}%`,
      sub: `${Math.round(wp.winProbNoPlan * 100)}% without it`,
    },
    { label: "Moves pending", value: `${pending}`, sub: `${league.movesUsed}/${league.moveCap} used` },
  ];

  return (
    <div className="plan-summary">
      {cells.map((c) => (
        <div key={c.label} className="plan-summary-cell">
          <span className="plan-summary-label">{c.label}</span>
          <span className={`plan-summary-value ${c.tone || ""}`}>{c.value}</span>
          {c.sub && <span className="plan-summary-sub">{c.sub}</span>}
        </div>
      ))}
    </div>
  );
}

function WeekStrip({ league }) {
  const { week, plan } = league;
  const active = plan.moves.filter((m) => !["SKIPPED", "DONE"].includes(m.status));

  return (
    <div className="week-strip">
      {week.days.map((day, i) => {
        const lineup = plan.lineup[i] || {};
        const moves = active.filter((m) => m.fireDayIndex === i);
        const isToday = i === week.todayIndex;
        return (
          <div
            key={day}
            className={`week-col ${lineup.played ? "col-past" : ""} ${isToday ? "col-today" : ""}`}
          >
            <div className="week-col-head">
              <span className="week-col-day">{day}</span>
              <span className="week-col-date">{week.dates[i]}</span>
            </div>
            <div className="week-col-body">
              <span className="week-starters">
                <strong>{lineup.starters ?? "-"}</strong> starting
              </span>
              {(lineup.openSlots || []).length > 0 ? (
                <div className="week-open">
                  {lineup.openSlots.map((s) => (
                    <span key={s} className="week-open-slot">
                      {s}
                    </span>
                  ))}
                </div>
              ) : (
                <span className="week-full">lineup full</span>
              )}
              {moves.map((m) => (
                <div key={m.id} className="week-move">
                  <span className="week-move-add">+{m.add.name.split(" ").slice(-1)[0]}</span>
                  <span className="week-move-gain">+{m.gain.toFixed(1)}</span>
                </div>
              ))}
            </div>
            {isToday && <span className="week-today-tag">today</span>}
          </div>
        );
      })}
    </div>
  );
}

function MoveActions({ league, move }) {
  const { setMoveStatus } = useAgentStore();
  const set = (s) => setMoveStatus(league.id, move.id, s);

  if (league.status === "STOPPED" || move.status === "FROZEN") {
    return <span className="move-locked">agent halted, resume to act</span>;
  }

  switch (move.status) {
    case "AWAITING_APPROVAL":
      return (
        <>
          <button className="agent-btn agent-btn-go" onClick={() => set("SCHEDULED")}>
            Approve
          </button>
          <button className="agent-btn" onClick={() => set("HELD")}>
            Hold
          </button>
          <button className="agent-btn agent-btn-kill" onClick={() => set("SKIPPED")}>
            Reject
          </button>
        </>
      );
    case "HELD":
      return (
        <>
          <button className="agent-btn agent-btn-go" onClick={() => set("SCHEDULED")}>
            Schedule
          </button>
          <button className="agent-btn agent-btn-kill" onClick={() => set("SKIPPED")}>
            Cancel
          </button>
        </>
      );
    case "DONE":
    case "SKIPPED":
      return (
        <button className="agent-btn" onClick={() => set("SCHEDULED")}>
          Undo
        </button>
      );
    default:
      return (
        <>
          <button className="agent-btn agent-btn-go" onClick={() => set("DONE")}>
            Fire now
          </button>
          <button className="agent-btn" onClick={() => set("HELD")}>
            Hold
          </button>
          <button className="agent-btn agent-btn-kill" onClick={() => set("SKIPPED")}>
            Cancel
          </button>
        </>
      );
  }
}

function PlannedMove({ league, move }) {
  const meta = MOVE_STATUS_META[move.status] || MOVE_STATUS_META.SCHEDULED;
  const dim = move.status === "SKIPPED" || move.status === "DONE";

  return (
    <div className={`plan-move ${dim ? "plan-move-dim" : ""}`}>
      <div className="plan-move-head">
        <span className="plan-move-fire">{move.fireDay}</span>
        <div className="plan-move-players">
          <div className="plan-move-line">
            <span className="plan-move-sign sign-add">+</span>
            <span className="plan-move-name">{move.add.name}</span>
            <span className="plan-move-detail">
              {move.add.pos} · {move.add.team} · {move.add.gamesLeft}g · {move.add.projWeek.toFixed(1)} proj
            </span>
          </div>
          <div className="plan-move-line">
            <span className="plan-move-sign sign-drop">−</span>
            <span className="plan-move-name plan-move-name-drop">{move.drop.name}</span>
            <span className="plan-move-detail">
              {move.drop.pos} · {move.drop.team} · {move.drop.gamesLeft}g · {move.drop.projWeek.toFixed(1)} proj
            </span>
          </div>
        </div>
        <div className="plan-move-numbers">
          <span className="plan-move-gain">+{move.gain.toFixed(1)}</span>
          <span className="plan-move-conf">conf {Math.round(move.confidence * 100)}%</span>
        </div>
        <span className={`plan-move-status ${meta.cls}`}>{meta.label}</span>
      </div>

      <div className="plan-move-body">
        <div className="plan-move-why">
          <span className="plan-why-label">Why</span>
          <p>{move.rationale}</p>
        </div>
        <div className="plan-move-why">
          <span className="plan-why-label">Why {move.fireDay.toLowerCase() === "now" ? "now" : `on ${move.fireDay}`}</span>
          <p>{move.deferReason}</p>
        </div>
      </div>

      <div className="plan-move-foot">
        <MoveActions league={league} move={move} />
      </div>
    </div>
  );
}

/* Grid view: how each add and drop lands on the remaining schedule. */

function GridCell({ game, state, fireStart }) {
  const cls = `gcell ${state} ${fireStart ? "gcell-fire" : ""}`;
  if (!game) return <div className={`${cls} gcell-none`}>·</div>;
  return (
    <div className={cls}>
      <span className="gcell-opp">{game.opp}</span>
      <span className="gcell-fpts">{game.fpts.toFixed(1)}</span>
    </div>
  );
}

function MoveScheduleBlock({ league, move, dayIdx }) {
  const { week, plan } = league;
  const meta = MOVE_STATUS_META[move.status] || MOVE_STATUS_META.SCHEDULED;
  const cols = `minmax(140px, 1fr) repeat(${dayIdx.length}, minmax(62px, 1fr))`;

  const net = (i) =>
    i < move.fireDayIndex
      ? 0
      : (move.add.schedule[i]?.fpts || 0) - (move.drop.schedule[i]?.fpts || 0);

  return (
    <div className={`sgrid-block ${move.status === "SKIPPED" ? "plan-move-dim" : ""}`}>
      <div className="sgrid-head">
        <span className="plan-move-fire">{move.fireDay}</span>
        <span className="sgrid-title">
          <span className="move-add">+{move.add.name}</span>
          <span className="sgrid-slash">/</span>
          <span className="move-drop">−{move.drop.name}</span>
        </span>
        <span className="plan-move-conf">conf {Math.round(move.confidence * 100)}%</span>
        <span className="plan-move-gain">+{move.gain.toFixed(1)}</span>
        <span className={`plan-move-status ${meta.cls}`}>{meta.label}</span>
      </div>

      <div className="sgrid" style={{ gridTemplateColumns: cols }}>
        <div className="sgrid-corner" />
        {dayIdx.map((i) => (
          <div key={i} className={`sgrid-day ${i === move.fireDayIndex ? "gcell-fire" : ""}`}>
            <span className="sgrid-day-name">{week.days[i]}</span>
            <span className="sgrid-day-date">{week.dates[i].split(" ")[1]}</span>
            {i === move.fireDayIndex && <span className="sgrid-fire-tag">fires</span>}
          </div>
        ))}

        <div className="sgrid-label sgrid-label-dim">Open slots</div>
        {dayIdx.map((i) => {
          const open = plan.lineup[i]?.openSlots || [];
          return (
            <div key={i} className={`gcell gcell-slots ${i === move.fireDayIndex ? "gcell-fire" : ""}`}>
              {open.length ? open.join(" ") : "full"}
            </div>
          );
        })}

        <div className="sgrid-label">
          <span className="move-add">+{move.add.name}</span>
          <span className="sgrid-label-sub">
            {move.add.pos} · {move.add.team}
          </span>
        </div>
        {dayIdx.map((i) => (
          <GridCell
            key={i}
            game={move.add.schedule[i]}
            state={i < move.fireDayIndex ? "gs-pending" : "gs-gain"}
            fireStart={i === move.fireDayIndex}
          />
        ))}

        <div className="sgrid-label">
          <span className="move-drop">−{move.drop.name}</span>
          <span className="sgrid-label-sub">
            {move.drop.pos} · {move.drop.team}
          </span>
        </div>
        {dayIdx.map((i) => (
          <GridCell
            key={i}
            game={move.drop.schedule[i]}
            state={i < move.fireDayIndex ? "gs-kept" : "gs-lost"}
            fireStart={i === move.fireDayIndex}
          />
        ))}

        <div className="sgrid-label sgrid-label-dim">Net</div>
        {dayIdx.map((i) => {
          const n = net(i);
          return (
            <div
              key={i}
              className={`gcell gcell-net ${n > 0 ? "pos" : n < 0 ? "neg" : "zero"} ${
                i === move.fireDayIndex ? "gcell-fire" : ""
              }`}
            >
              {n === 0 ? "0" : `${n > 0 ? "+" : ""}${n.toFixed(1)}`}
            </div>
          );
        })}
      </div>

      <div className="sgrid-foot">
        <span className="sgrid-defer">{move.deferReason}</span>
        <div className="sgrid-actions">
          <MoveActions league={league} move={move} />
        </div>
      </div>
    </div>
  );
}

function ScheduleGrid({ league, moves }) {
  const dayIdx = league.week.days.map((_, i) => i).slice(league.week.todayIndex);

  return (
    <div className="sgrid-list">
      <div className="sgrid-legend">
        <span className="lg lg-gain">gained</span>
        <span className="lg lg-kept">kept</span>
        <span className="lg lg-lost">lost</span>
        <span className="lg lg-pending">not yet ours</span>
      </div>
      {moves.map((m) => (
        <MoveScheduleBlock key={m.id} league={league} move={m} dayIdx={dayIdx} />
      ))}
    </div>
  );
}

function RejectedList({ rejected }) {
  const [open, setOpen] = useState(false);
  if (!rejected.length) return null;

  return (
    <div className="rejected-block">
      <button className="rejected-toggle" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} Considered and rejected ({rejected.length})
      </button>
      {open && (
        <div className="rejected-list">
          {rejected.map((x) => (
            <div key={x.id} className="rejected-row">
              <span className="rejected-summary">{x.summary}</span>
              <span className={`rejected-gain ${x.gain >= 0 ? "pos" : "neg"}`}>
                {x.gain >= 0 ? "+" : ""}
                {x.gain.toFixed(1)}
              </span>
              <span className="rejected-reason">{x.reason}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PlanTab({ league }) {
  const [view, setView] = useState("grid");
  const moves = league.plan.moves;

  return (
    <>
      <PlanSummary league={league} />

      <div className="agent-section">
        <h2 className="agent-section-title">The week, day by day</h2>
        <WeekStrip league={league} />
      </div>

      <div className="agent-section">
        <div className="agent-section-head">
          <h2 className="agent-section-title">Planned transactions</h2>
          <div className="view-switch">
            {[
              { id: "grid", label: "Grid" },
              { id: "list", label: "List" },
            ].map((v) => (
              <button
                key={v.id}
                className={`filter-chip ${view === v.id ? "chip-active" : ""}`}
                onClick={() => setView(v.id)}
              >
                {v.label}
              </button>
            ))}
          </div>
        </div>

        {moves.length === 0 ? (
          <Card>
            <p className="agent-empty">
              No moves planned. {league.status === "STOPPED" ? "Last plan is stale." : "Roster is at its optimum."}
            </p>
          </Card>
        ) : view === "grid" ? (
          <ScheduleGrid league={league} moves={moves} />
        ) : (
          <div className="plan-move-list">
            {moves.map((m) => (
              <PlannedMove key={m.id} league={league} move={m} />
            ))}
          </div>
        )}
        <RejectedList rejected={league.plan.rejected} />
      </div>
    </>
  );
}

/* ================================ My team ================================ */

const BENCH_SLOTS = ["BN", "IR", "IR+", "NA"];

function RosterRows({ league, players }) {
  const { togglePlayerTag } = useAgentStore();

  return players.map((p) => (
    <tr key={p.name} className={p.status ? "roster-row-out" : ""}>
      <td className="rt-slot">{p.slot}</td>
      <td className="rt-name">
        {p.name}
        {p.status && <span className={`rt-status st-${p.status.toLowerCase()}`}>{p.status}</span>}
        {p.tag && <span className={`rt-tag tag-${p.tag.replace(/\s/g, "-")}`}>{p.tag}</span>}
      </td>
      <td>{p.pos}</td>
      <td className="rt-mono">{p.team}</td>
      <td className="rt-mono rt-right">{p.gamesLeft}</td>
      <td className="rt-mono rt-right">{p.fptsGp.toFixed(1)}</td>
      <td className="rt-mono rt-right rt-proj">{p.projWeek.toFixed(1)}</td>
      <td className="rt-right">
        <button
          className={`rt-protect ${p.tag === "protected" ? "is-protected" : ""}`}
          onClick={() => togglePlayerTag(league.id, p.name, "protected")}
        >
          {p.tag === "protected" ? "Protected" : "Protect"}
        </button>
      </td>
    </tr>
  ));
}

function TeamTab({ league }) {
  const active = league.roster.filter((p) => !BENCH_SLOTS.includes(p.slot));
  const bench = league.roster.filter((p) => BENCH_SLOTS.includes(p.slot));
  const total = league.roster.reduce((n, p) => n + p.projWeek, 0);
  const activeTotal = active.reduce((n, p) => n + p.projWeek, 0);
  const protectedCount = league.roster.filter((p) => p.tag === "protected").length;
  const dropCandidates = league.roster.filter((p) => p.tag === "drop candidate").length;

  return (
    <>
      <div className="plan-summary">
        <div className="plan-summary-cell">
          <span className="plan-summary-label">Roster projection, rest of week</span>
          <span className="plan-summary-value pos">{total.toFixed(1)}</span>
          <span className="plan-summary-sub">{activeTotal.toFixed(1)} from starters</span>
        </div>
        <div className="plan-summary-cell">
          <span className="plan-summary-label">Players</span>
          <span className="plan-summary-value">{league.roster.length}</span>
          <span className="plan-summary-sub">
            {active.length} starting · {bench.length} bench/IR
          </span>
        </div>
        <div className="plan-summary-cell">
          <span className="plan-summary-label">Protected</span>
          <span className="plan-summary-value">{protectedCount}</span>
          <span className="plan-summary-sub">not droppable</span>
        </div>
        <div className="plan-summary-cell">
          <span className="plan-summary-label">Drop candidates</span>
          <span className="plan-summary-value neg">{dropCandidates}</span>
          <span className="plan-summary-sub">lowest ranked</span>
        </div>
      </div>

      <div className="agent-section">
        <h2 className="agent-section-title">Roster</h2>
        <Card>
          <table className="roster-table-agent">
            <thead>
              <tr>
                <th>Slot</th>
                <th>Player</th>
                <th>Pos</th>
                <th>Team</th>
                <th className="rt-right">Games left</th>
                <th className="rt-right">FPTS/GP</th>
                <th className="rt-right">Proj week</th>
                <th className="rt-right">Agent</th>
              </tr>
            </thead>
            <tbody>
              <RosterRows league={league} players={active} />
              {bench.length > 0 && (
                <tr className="roster-split">
                  <td colSpan={8}>Bench / IR</td>
                </tr>
              )}
              <RosterRows league={league} players={bench} />
            </tbody>
          </table>
        </Card>
      </div>
    </>
  );
}

/* ================================ Activity ================================ */

function ActionBadge({ action }) {
  const map = {
    ADDED: "badge-added",
    QUEUED: "badge-queued",
    IGNORED: "badge-ignored",
    LINEUP: "badge-lineup",
    PAUSED: "badge-queued",
    STOPPED: "badge-error",
  };
  return <span className={`agent-action-badge ${map[action] || ""}`}>{action}</span>;
}

function TriggerDot({ trigger }) {
  const meta = TRIGGER_META[trigger] || { glyph: "•", color: "#7c5cfc" };
  return (
    <span
      className="timeline-dot"
      style={{ color: meta.color, boxShadow: `0 0 14px ${meta.color}66, inset 0 0 0 1px ${meta.color}` }}
    >
      {meta.glyph}
    </span>
  );
}

function TimelineEntry({ entry }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className={`timeline-entry ${expanded ? "expanded" : ""}`}>
      <div className="timeline-row" onClick={() => setExpanded(!expanded)}>
        <TriggerDot trigger={entry.trigger} />
        <span className="timeline-time">{entry.time}</span>
        <span className="agent-trigger-badge">{entry.trigger.replace("_", " ")}</span>
        <span className="timeline-headline">{entry.headline}</span>
        <ActionBadge action={entry.action} />
        {entry.score > 0 && <span className="timeline-score">+{entry.score.toFixed(1)}</span>}
        {entry.outcome && (
          <span className={`timeline-outcome ${entry.outcome.actual >= 0 ? "outcome-hit" : "outcome-miss"}`}>
            actual: {entry.outcome.actual >= 0 ? "+" : ""}
            {entry.outcome.actual.toFixed(1)}
          </span>
        )}
      </div>
      {expanded && (
        <div className="timeline-detail">
          <div className="detail-block">
            <div className="detail-label">Source</div>
            <div className="detail-value">{entry.source}</div>
          </div>
          <div className="detail-block">
            <div className="detail-label">Action</div>
            <div className="detail-value">{entry.actionDetail}</div>
          </div>
          <div className="detail-block">
            <div className="detail-label">Reasoning</div>
            <div className="detail-value">{entry.reasoning}</div>
          </div>
          {entry.candidates && (
            <div className="detail-block">
              <div className="detail-label">Candidates considered</div>
              <table className="detail-table">
                <thead>
                  <tr>
                    <th>Player</th>
                    <th>GP</th>
                    <th>FPTS/GP</th>
                    <th>Score</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {entry.candidates.map((c, i) => (
                    <tr key={i} className={c.picked ? "row-picked" : ""}>
                      <td>{c.name}</td>
                      <td>{c.games}</td>
                      <td>{c.fpts.toFixed(1)}</td>
                      <td>+{c.score.toFixed(1)}</td>
                      <td>{c.picked ? "✓ picked" : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {entry.outcome && (
            <div className="detail-block">
              <div className="detail-label">Outcome</div>
              <div className="detail-value">{entry.outcome.note}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ActivityTab({ league }) {
  const [filter, setFilter] = useState("all");
  const entries = league.transactions.filter((e) => {
    if (filter === "transactions") return ["ADDED", "QUEUED"].includes(e.action);
    if (filter === "passed") return ["IGNORED"].includes(e.action);
    return true;
  });

  return (
    <>
      <div className="agent-section">
        <h2 className="agent-section-title">What the agent has done</h2>
        <div className="activity-filters">
          {[
            { id: "all", label: "Everything" },
            { id: "transactions", label: "Transactions" },
            { id: "passed", label: "Passed on" },
          ].map((f) => (
            <button
              key={f.id}
              className={`filter-chip ${filter === f.id ? "chip-active" : ""}`}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <Card>
          {entries.length === 0 ? (
            <p className="agent-empty">Nothing logged in this category.</p>
          ) : (
            <div className="timeline-list">
              {entries.map((e) => (
                <TimelineEntry key={e.id} entry={e} />
              ))}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}

function PulseTab({ league }) {
  return (
    <div className="agent-section">
      <h2 className="agent-section-title">Other managers' moves, graded</h2>
      <Card>
        <div className="pulse-list">
          {league.pulse.map((p, i) => (
            <div key={i} className="pulse-row">
              <div className="pulse-left">
                <div className="pulse-manager">{p.manager}</div>
                <div className="pulse-action">{p.action}</div>
                <div className="pulse-note">{p.note}</div>
              </div>
              <div className="pulse-right">
                <span className={`pulse-grade grade-${p.gradeClass}`}>{p.grade}</span>
                <span className="pulse-time">{p.time}</span>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

/* ================================ Watchlist ================================ */

function WatchCard({ title, rows, empty }) {
  return (
    <Card>
      <div className="card-section-header">{title}</div>
      <div className="watching-list">
        {rows.length === 0 ? <p className="agent-empty">{empty}</p> : rows}
      </div>
    </Card>
  );
}

function WatchlistTab({ league }) {
  const w = league.watchlist;

  return (
    <div className="watching-grid">
      <WatchCard
        title="Rising free agents"
        empty="Nothing trending up."
        rows={w.risingFA.map((p, i) => (
          <div key={i} className="watching-row">
            <div>
              <span className="watching-name">{p.name}</span>
              <span className="watching-team">{p.team}</span>
            </div>
            <span className="watching-trend trend-up">{p.trend}</span>
            <span className="watching-note">{p.note}</span>
          </div>
        ))}
      />

      <WatchCard
        title="Roster players at risk"
        empty="No roster player is trending down."
        rows={w.atRisk.map((p, i) => (
          <div key={i} className="watching-row">
            <div>
              <span className="watching-name">{p.name}</span>
              <span className="watching-team">{p.team}</span>
            </div>
            <span className="watching-trend trend-down">{p.trend}</span>
            <span className="watching-note">{p.note}</span>
          </div>
        ))}
      />

      <WatchCard
        title="Confirmed goalie starts"
        empty="No confirmed starters yet today."
        rows={w.goalies.map((g, i) => (
          <div key={i} className="watching-row">
            <div>
              <span className="watching-name">{g.goalie}</span>
              <span className="watching-team">
                {g.team} {g.opponent}
              </span>
            </div>
            {g.rostered ? (
              <span className="watching-tag tag-owned">rostered</span>
            ) : (
              <span className="watching-trend trend-up">stream {g.streamScore.toFixed(1)}</span>
            )}
          </div>
        ))}
      />

      <WatchCard
        title="Active injuries"
        empty="No injuries affecting this roster."
        rows={w.injuries.map((p, i) => (
          <div key={i} className="watching-row">
            <div>
              <span className="watching-name">{p.name}</span>
              <span className="watching-team">{p.team}</span>
            </div>
            <span className={`watching-tag tag-${p.status.toLowerCase()}`}>{p.status}</span>
            <span className="watching-note">{p.note}</span>
          </div>
        ))}
      />
    </div>
  );
}

/* ================================== Page ================================== */

export default function Agent() {
  const { leagueId } = useParams();
  const { leagues, getLeague } = useAgentStore();
  const [tab, setTab] = useState("plan");

  const league = getLeague(leagueId) || leagues[0];
  if (!league) return null;

  const pendingApprovals = league.plan.moves.filter((m) => m.status === "AWAITING_APPROVAL").length;

  return (
    <div className="agent-page">
      <Header league={league} />
      <ControlBar league={league} />
      <MatchupBanner league={league} />

      <div className="agent-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`agent-tab ${tab === t.id ? "tab-active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {t.id === "plan" && pendingApprovals > 0 && (
              <span className="tab-badge">{pendingApprovals}</span>
            )}
          </button>
        ))}
      </div>

      {tab === "plan" && <PlanTab league={league} />}
      {tab === "team" && <TeamTab league={league} />}
      {tab === "activity" && <ActivityTab league={league} />}
      {tab === "pulse" && <PulseTab league={league} />}
      {tab === "watchlist" && <WatchlistTab league={league} />}
    </div>
  );
}

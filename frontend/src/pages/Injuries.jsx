import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Card from "../components/Card";
import { getAllInjuries } from "../api/client";
import "./Injuries.css";

const TIMELINE_DAYS = 28;

const SEVERITY_COLOR = {
  season: "#ef4444",
  "month-plus": "#f97316",
  "week-to-week": "#eab308",
  "day-to-day": "#7c5cfc",
  unknown: "#94a3b8",
};

const SEVERITY_SHORT = {
  season: "SZN",
  "month-plus": "MTH+",
  "week-to-week": "WTW",
  "day-to-day": "DTD",
  unknown: "UNK",
};

function daysFromNow(iso) {
  if (!iso) return null;
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((new Date(iso + "T00:00:00") - now) / 86400000);
}

function getDateTicks() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const ticks = [];
  for (let d = 0; d <= TIMELINE_DAYS; d += 7) {
    const dt = new Date(today);
    dt.setDate(dt.getDate() + d);
    ticks.push({
      day: d,
      pct: (d / TIMELINE_DAYS) * 100,
      label: d === 0
        ? "Today"
        : dt.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    });
  }
  return ticks;
}

function GanttBar({ injury }) {
  const soonest = daysFromNow(injury.soonest_return);
  const latest = daysFromNow(injury.latest_return);
  const color = SEVERITY_COLOR[injury.severity] || SEVERITY_COLOR.unknown;

  if (soonest === null) {
    return (
      <div className="gantt-cell">
        <span className="gantt-no-eta">
          {injury.severity === "season" ? "Out for season" : "No timeline"}
        </span>
      </div>
    );
  }

  const startPct = Math.max(0, Math.min(100, (soonest / TIMELINE_DAYS) * 100));
  const endPct = Math.max(
    startPct + 2,
    Math.min(100, ((latest ?? soonest) / TIMELINE_DAYS) * 100)
  );

  const retLabel =
    soonest <= 0 ? "Now" : soonest === 1 ? "1 day" : `${soonest}d`;

  return (
    <div className="gantt-cell">
      <div
        className="gantt-fill"
        style={{
          left: `${startPct}%`,
          width: `${endPct - startPct}%`,
          background: color,
        }}
      />
      <span
        className="gantt-label"
        style={{ left: `${Math.min(startPct, 85)}%` }}
      >
        {retLabel}
      </span>
    </div>
  );
}

function DateAxis() {
  const ticks = getDateTicks();
  return (
    <div className="gantt-axis">
      {ticks.map((t) => (
        <span
          key={t.day}
          className={`gantt-axis-label ${t.day === 0 ? "gantt-axis-today" : ""}`}
          style={{ left: `${t.pct}%` }}
        >
          {t.label}
        </span>
      ))}
    </div>
  );
}

function GridLines() {
  const ticks = getDateTicks();
  return (
    <>
      {ticks.map((t) => (
        <div
          key={t.day}
          className={`gantt-gridline ${t.day === 0 ? "gantt-gridline-today" : ""}`}
          style={{ left: `${t.pct}%` }}
        />
      ))}
    </>
  );
}

function PlayerRow({ injury, onPlayerClick }) {
  const clickable = !!injury.nhl_id;
  const sevColor = SEVERITY_COLOR[injury.severity] || SEVERITY_COLOR.unknown;

  return (
    <div
      className={`gantt-row ${clickable ? "gantt-row-click" : ""}`}
      onClick={() => clickable && onPlayerClick(injury.nhl_id)}
    >
      <div className="gantt-player">
        {injury.headshot ? (
          <img
            src={injury.headshot}
            alt=""
            className="gantt-avatar"
            onError={(e) => {
              if (injury.team_abbrev) {
                e.target.src = `https://assets.nhle.com/logos/nhl/svg/${injury.team_abbrev}_dark.svg`;
                e.target.className = "gantt-avatar gantt-avatar-logo";
              } else {
                e.target.style.display = "none";
              }
            }}
          />
        ) : (
          <div className="gantt-avatar-empty" />
        )}
        <div className="gantt-info">
          <div className="gantt-name-row">
            <span className="gantt-name">{injury.player_name}</span>
            {injury.fpts_per_gp != null && (
              <span className="gantt-fppg">{injury.fpts_per_gp.toFixed(1)}</span>
            )}
          </div>
          <div className="gantt-meta">
            <span className="gantt-team">{injury.team_abbrev}{injury.position ? ` · ${injury.position.toUpperCase()}` : ""}</span>
            <span className="gantt-sev" style={{ background: sevColor + "20", color: sevColor }}>
              {SEVERITY_SHORT[injury.severity] || "UNK"}
            </span>
            {injury.body_part && <span className="gantt-bp">{injury.body_part}</span>}
          </div>
        </div>
      </div>

      <div className="gantt-timeline">
        <GridLines />
        <GanttBar injury={injury} />
      </div>
    </div>
  );
}

function Section({ title, subtitle, items, defaultOpen, onPlayerClick }) {
  const [open, setOpen] = useState(defaultOpen);

  if (items.length === 0) return null;

  return (
    <div className="inj-section">
      <div className="inj-section-header" onClick={() => setOpen(!open)}>
        <span className={`inj-arrow ${open ? "open" : ""}`}>&#9654;</span>
        <h2>{title}</h2>
        <span className="inj-section-count">{items.length}</span>
        {subtitle && <span className="inj-section-sub">{subtitle}</span>}
      </div>
      {open && (
        <Card>
          <div className="gantt-chart">
            <div className="gantt-header">
              <div className="gantt-header-spacer" />
              <div className="gantt-header-axis">
                <DateAxis />
              </div>
            </div>
            {items.map((inj, i) => (
              <PlayerRow
                key={`${inj.nhl_id || inj.player_name}-${i}`}
                injury={inj}
                onPlayerClick={onPlayerClick}
              />
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

export default function Injuries() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [minFppg, setMinFppg] = useState(2.5);
  const [pendingFppg, setPendingFppg] = useState(2.5);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getAllInjuries().then((data) => {
      if (cancelled) return;
      setItems(data?.items || []);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  const hasRosterData = items.some((i) => i.roster_status);
  const dirty = pendingFppg !== minFppg;

  const passesFilter = (i) =>
    i.fpts_per_gp == null
      ? i.roster_status === "mine"
      : i.fpts_per_gp >= minFppg;

  const myInjured = items.filter((i) => i.roster_status === "mine" && passesFilter(i));
  const returning = items.filter((i) => i.severity !== "season" && i.soonest_return);
  const faReturning = returning
    .filter((i) => i.roster_status === "free" && passesFilter(i))
    .sort((a, b) => (b.fpts_per_gp || 0) - (a.fpts_per_gp || 0));
  const outForSeason = items
    .filter((i) => i.severity === "season" && passesFilter(i))
    .sort((a, b) => (b.fpts_per_gp || 0) - (a.fpts_per_gp || 0));
  const unknownTimeline = items
    .filter((i) => i.severity !== "season" && !i.soonest_return && passesFilter(i))
    .sort((a, b) => (b.fpts_per_gp || 0) - (a.fpts_per_gp || 0));

  const onPlayerClick = (id) => navigate(`/players/${id}`);

  return (
    <div className="injuries-page">
      <h1>Injury Report</h1>

      <div className="inj-controls">
        <div className="inj-fppg-control">
          <label>Min FPPG</label>
          <input
            type="range"
            min={0}
            max={4}
            step={0.1}
            value={pendingFppg}
            onChange={(e) => setPendingFppg(parseFloat(e.target.value))}
          />
          <span className="inj-fppg-display">
            {pendingFppg === 0 ? "All" : `${pendingFppg.toFixed(1)}+`}
          </span>
          <button
            className={`inj-apply-btn ${dirty ? "dirty" : ""}`}
            onClick={() => setMinFppg(pendingFppg)}
            disabled={!dirty}
          >
            Apply
          </button>
        </div>
      </div>

      {loading ? (
        <Card><div className="placeholder-shimmer" style={{ height: 400 }} /></Card>
      ) : (
        <>
          {hasRosterData && (
            <Section
              title="My Injuries"
              subtitle="Players on my roster currently injured"
              items={myInjured}
              defaultOpen={true}
              onPlayerClick={onPlayerClick}
            />
          )}

          <Section
            title="Stash Targets"
            subtitle={hasRosterData ? "Free agents returning soon — sorted by FPPG" : "Players returning soon — sorted by FPPG"}
            items={hasRosterData ? faReturning : returning.filter(passesFilter).sort((a, b) => (b.fpts_per_gp || 0) - (a.fpts_per_gp || 0))}
            defaultOpen={true}
            onPlayerClick={onPlayerClick}
          />

          <Section
            title="Unknown Timeline"
            items={unknownTimeline}
            defaultOpen={false}
            onPlayerClick={onPlayerClick}
          />

          <Section
            title="Out for Season"
            items={outForSeason}
            defaultOpen={false}
            onPlayerClick={onPlayerClick}
          />
        </>
      )}
    </div>
  );
}

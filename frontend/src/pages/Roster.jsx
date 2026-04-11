import { useState } from "react";
import { useNavigate } from "react-router-dom";
import Card from "../components/Card";
import { useApi } from "../hooks/useApi";
import {
  getYahooStatus,
  getYahooLeagues,
  getYahooRosterWeek,
} from "../api/client";
import "./Roster.css";

function DayView({ weekSummary }) {
  return (
    <div className="week-schedule">
      {weekSummary.map((day) => (
        <div key={day.date} className={`week-day ${day.players_playing === 0 ? "week-day-empty" : ""}`}>
          <div className="day-label">{day.day.slice(0, 3)}</div>
          <div className="day-date">
            {new Date(day.date + "T12:00:00").toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
            })}
          </div>
          <div className="day-playing">
            {day.players_playing > 0
              ? `${day.players_playing} playing`
              : "Off day"}
          </div>
          <div className="day-positions">
            {(day.open_slots || []).length > 0 ? (
              <>
                <span className="open-label">Open:</span>
                {day.open_slots.map((pos) => (
                  <span key={pos} className="pos-badge pos-open">
                    {pos}
                  </span>
                ))}
              </>
            ) : day.players_playing > 0 ? (
              <span className="slots-full">All filled</span>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}

function PositionView({ weekSummary }) {
  const positions = ["C", "LW", "RW", "D", "G", "UTIL"];

  // Build position -> which days have open slots
  const positionDays = {};
  for (const pos of positions) {
    positionDays[pos] = weekSummary.map((day) => ({
      dayLabel: day.day.slice(0, 3),
      isOpen: (day.open_slots || []).includes(pos),
    }));
  }

  return (
    <div className="week-schedule">
      {positions.map((pos) => {
        const days = positionDays[pos];
        const openCount = days.filter((d) => d.isOpen).length;

        return (
          <div key={pos} className={`week-day ${openCount === 0 ? "week-day-empty" : ""}`}>
            <div className="day-label">{pos}</div>
            <div className="day-playing">
              {openCount > 0 ? `${openCount} open` : "filled"}
            </div>
            <div className="day-positions">
              {days.map((d, i) => (
                <span
                  key={i}
                  className={`pos-badge ${d.isOpen ? "pos-open" : "pos-filled"}`}
                >
                  {d.dayLabel}
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function WeekSchedule({ weekSummary }) {
  const [view, setView] = useState("day");

  if (!weekSummary || weekSummary.length === 0) return null;

  return (
    <div>
      <div className="view-toggle">
        <button
          className={`toggle-btn ${view === "day" ? "toggle-active" : ""}`}
          onClick={() => setView("day")}
        >
          Day
        </button>
        <button
          className={`toggle-btn ${view === "position" ? "toggle-active" : ""}`}
          onClick={() => setView("position")}
        >
          Position
        </button>
      </div>
      {view === "day" ? (
        <DayView weekSummary={weekSummary} />
      ) : (
        <PositionView weekSummary={weekSummary} />
      )}
    </div>
  );
}

function RosterTable({ roster, weekSummary }) {
  const navigate = useNavigate();

  if (!roster || roster.length === 0) {
    return <p className="empty-state">No roster data</p>;
  }

  const dayHeaders = (weekSummary || []).map((d) => d.day.slice(0, 3));
  const inactiveSlots = ["BN", "IR", "IR+", "NA"];
  const active = roster.filter((p) => !inactiveSlots.includes(p.selected_position));
  const inactive = roster.filter((p) => inactiveSlots.includes(p.selected_position));
  const colCount = 7 + dayHeaders.length;

  return (
    <table className="roster-table">
      <thead>
        <tr>
          <th>Slot</th>
          <th>Player</th>
          <th>Pos</th>
          <th>Team</th>
          <th>TOI</th>
          <th className="col-fpts">FPTS/GP</th>
          <th className="col-fpts">Wk GP</th>
          {dayHeaders.map((d) => (
            <th key={d} className="col-game">
              {d}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {active.map((p) => {
          return (
          <tr
            key={p.name}
            className="clickable-row"
            onClick={() => p.nhl_id && navigate(`/players/${p.nhl_id}`)}
          >
            <td className="col-slot">{p.selected_position}</td>
            <td className="col-name">
              {p.name}
              {p.status && <span className="status-badge">{p.status}</span>}
            </td>
            <td>{p.position}</td>
            <td>{p.team}</td>
            <td>{p.avg_toi ?? "—"}</td>
            <td className="col-fpts">{p.fpts_per_gp ?? "—"}</td>
            <td className="col-fpts">{p.games_this_week}</td>
            {(p.schedule || []).map((s) => (
              <td key={s.date} className="col-game">
                {s.has_game ? (
                  <span className="game-matchup">
                    {s.is_home ? "vs" : "@"}
                    <br />
                    {s.opponent}
                  </span>
                ) : (
                  <span className="no-game">—</span>
                )}
              </td>
            ))}
          </tr>
          );
        })}
        {inactive.length > 0 && (
          <tr className="roster-divider">
            <td colSpan={colCount}></td>
          </tr>
        )}
        {inactive.map((p) => (
          <tr
            key={p.name}
            className="clickable-row player-inactive"
            onClick={() => p.nhl_id && navigate(`/players/${p.nhl_id}`)}
          >
            <td className="col-slot">{p.selected_position}</td>
            <td className="col-name">
              {p.name}
              {p.status && <span className="status-badge">{p.status}</span>}
            </td>
            <td>{p.position}</td>
            <td>{p.team}</td>
            <td>{p.avg_toi ?? "—"}</td>
            <td className="col-fpts">{p.fpts_per_gp ?? "—"}</td>
            <td className="col-fpts">{p.games_this_week}</td>
            {(p.schedule || []).map((s) => (
              <td key={s.date} className="col-game">
                {s.has_game ? (
                  <span className="game-matchup">
                    {s.is_home ? "vs" : "@"}
                    <br />
                    {s.opponent}
                  </span>
                ) : (
                  <span className="no-game">—</span>
                )}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function GoalieTable({ goalies, weekSummary }) {
  const navigate = useNavigate();

  if (!goalies || goalies.length === 0) {
    return <p className="empty-state">No goalies on roster</p>;
  }

  const dayHeaders = (weekSummary || []).map((d) => d.day.slice(0, 3));

  return (
    <table className="roster-table goalie-table">
      <thead>
        <tr>
          <th>Slot</th>
          <th>Goalie</th>
          <th>Team</th>
          <th>Wk GP</th>
          {dayHeaders.map((d) => (
            <th key={d} className="col-game">{d}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {goalies.map((p) => {
          const inactive = ["BN", "IR", "IR+", "NA"].includes(p.selected_position) || p.status;
          return (
            <tr
              key={p.name}
              className={`clickable-row ${inactive ? "player-inactive" : ""}`}
              onClick={() => p.nhl_id && navigate(`/players/${p.nhl_id}`)}
            >
              <td className="col-slot">{p.selected_position}</td>
              <td className="col-name">
                {p.name}
                {p.status && <span className="status-badge">{p.status}</span>}
              </td>
              <td>{p.team}</td>
              <td className="col-fpts">{p.games_this_week}</td>
              {(p.schedule || []).map((s) => (
                <td key={s.date} className="col-game">
                  {s.has_game ? (
                    <span className="game-matchup">
                      {s.is_home ? "vs" : "@"}
                      <br />
                      {s.opponent}
                    </span>
                  ) : (
                    <span className="no-game">—</span>
                  )}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function ConnectedRoster() {
  const { data: leagueData, loading: leaguesLoading } = useApi(getYahooLeagues);
  const leagueKey = leagueData?.leagues?.[0]?.league_key;

  const { data, loading } = useApi(
    () => (leagueKey ? getYahooRosterWeek(leagueKey) : Promise.resolve(null)),
    [leagueKey]
  );

  if (leaguesLoading || loading) {
    return <div className="placeholder-shimmer" style={{ height: 400 }} />;
  }

  if (!data || data.error) {
    return <p className="empty-state">{data?.error || "Failed to load roster"}</p>;
  }

  const goalies = data.roster.filter((p) => p.selected_position === "G");
  const skaters = data.roster.filter((p) => p.selected_position !== "G");

  return (
    <>
      <Card title="This Week's Slot Availability">
        <WeekSchedule weekSummary={data.week_summary} />
      </Card>

      <Card title={`${data.team_name} — Skaters`}>
        <RosterTable roster={skaters} weekSummary={data.week_summary} />
      </Card>

      <Card title="Goalies">
        <GoalieTable goalies={goalies} weekSummary={data.week_summary} />
      </Card>
    </>
  );
}

export default function Roster() {
  const { data: yahooStatus } = useApi(getYahooStatus);
  const connected = yahooStatus?.connected;

  return (
    <div className="roster-page">
      <h1>My Roster</h1>
      {connected ? (
        <ConnectedRoster />
      ) : (
        <Card title="Roster">
          <p className="empty-state">
            Connect your Yahoo Fantasy league to see your roster and projections.
          </p>
        </Card>
      )}
    </div>
  );
}

import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import hockeyStick from "../assets/hockey-stick.png";
import { useAgentStore } from "../state/agentContext";
import "./Layout.css";

const LEGACY_LINKS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/players", label: "Players" },
  { to: "/roster", label: "My Roster" },
  { to: "/adds", label: "Optimal Adds" },
  { to: "/trades", label: "Trade Targets" },
  { to: "/streamable-goalies", label: "Goalie Streams" },
  { to: "/injuries", label: "Injuries" },
];

export default function Layout() {
  const { leagues } = useAgentStore();
  const [legacyOpen, setLegacyOpen] = useState(false);

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="sidebar-logo">
          <span className="logo-text">PUCK</span>
          <span className="logo-accent">AGENT</span>
        </div>

        <div className="sidebar-nav">
          <NavLink to="/" className="nav-link" end>
            <span className="nav-icon">&#9632;</span>
            Fleet
          </NavLink>

          <div className="nav-group-label">Leagues</div>
          {leagues.map((l) => (
            <NavLink key={l.id} to={`/league/${l.id}`} className="nav-link nav-link-league">
              <span className={`nav-status-dot dot-${l.status.toLowerCase()}`} />
              <span className="nav-league-text">
                <span className="nav-league-name">{l.league}</span>
                <span className="nav-league-sub">
                  {l.record} · #{l.rank}
                </span>
              </span>
            </NavLink>
          ))}

          <button
            className={`nav-group-toggle ${legacyOpen ? "open" : ""}`}
            onClick={() => setLegacyOpen(!legacyOpen)}
          >
            <span className="nav-chevron">{legacyOpen ? "▾" : "▸"}</span>
            Legacy tools
          </button>
          {legacyOpen && (
            <div className="nav-legacy">
              {LEGACY_LINKS.map((l) => (
                <NavLink key={l.to} to={l.to} className="nav-link nav-link-sub">
                  {l.label}
                </NavLink>
              ))}
            </div>
          )}

          <NavLink to="/how-i-made-this" className="nav-link nav-link-meta">
            <span className="nav-icon">&#9671;</span>
            How I Made This
          </NavLink>
        </div>

        <div className="sidebar-footer">
          <div className="stick-container">
            <img src={hockeyStick} alt="" className="hockey-stick" draggable={false} />
          </div>
          <span className="version">v0.1 alpha</span>
        </div>
      </nav>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}

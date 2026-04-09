import { NavLink, Outlet } from "react-router-dom";
import hockeyStick from "../assets/hockey-stick.png";
import "./Layout.css";

export default function Layout() {
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
            Dashboard
          </NavLink>
          <NavLink to="/players" className="nav-link">
            <span className="nav-icon">&#9632;</span>
            Players
          </NavLink>
          <NavLink to="/roster" className="nav-link">
            <span className="nav-icon">&#9632;</span>
            My Roster
          </NavLink>
          <NavLink to="/adds" className="nav-link">
            <span className="nav-icon">&#9632;</span>
            Optimal Adds
          </NavLink>
          <NavLink to="/trades" className="nav-link">
            <span className="nav-icon">&#9632;</span>
            Trade Targets
          </NavLink>
        </div>
        <div className="sidebar-footer">
          <div className="stick-container">
            <img
              src={hockeyStick}
              alt=""
              className="hockey-stick"
              draggable={false}
            />
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

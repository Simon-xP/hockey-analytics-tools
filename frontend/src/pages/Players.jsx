import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  searchPlayers,
  getYahooStatus,
  getYahooLeagues,
  getYahooTrending,
} from "../api/client";
import Card from "../components/Card";
import "./Players.css";

function TrendingPlayers() {
  const { data: yahooStatus } = useQuery({
    queryKey: ["yahoo-status"],
    queryFn: getYahooStatus,
  });
  const { data: leagueData } = useQuery({
    queryKey: ["yahoo-leagues"],
    queryFn: getYahooLeagues,
    enabled: !!yahooStatus?.connected,
  });
  const leagueKey = leagueData?.leagues?.[0]?.league_key;
  const connected = yahooStatus?.connected && leagueKey;

  const { data, isLoading: loading } = useQuery({
    queryKey: ["yahoo-trending", leagueKey],
    queryFn: () => getYahooTrending(leagueKey, 20),
    enabled: !!connected,
  });

  const navigate = useNavigate();

  if (!connected) return null;
  if (loading) return <div className="placeholder-shimmer" style={{ height: 200 }} />;

  const players = data?.players || [];
  if (players.length === 0) return null;

  return (
    <Card title="Trending Players">
      <div className="trending-list">
        {players.map((p) => (
          <div
            key={p.player_key}
            className="trending-row"
            onClick={() => p.nhl_id && navigate(`/players/${p.nhl_id}`)}
          >
            <div className="trending-player">
              <span className="player-name">{p.name}</span>
              <span className="player-meta">
                {p.position} · {p.team}
                {p.status && <span className="status-badge">{p.status}</span>}
              </span>
            </div>
            <div className="trending-stats">
              <span className="pct-owned">{p.percent_owned}%</span>
              {p.ownership_delta !== 0 && p.ownership_delta !== null && (
                <span
                  className={`pct-delta ${
                    p.ownership_delta > 0 ? "delta-up" : "delta-down"
                  }`}
                >
                  {p.ownership_delta > 0 ? "+" : ""}
                  {p.ownership_delta}%
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function Players() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const navigate = useNavigate();

  async function handleSearch(e) {
    const q = e.target.value;
    setQuery(q);

    if (q.length < 2) {
      setResults([]);
      return;
    }

    setSearching(true);
    try {
      const data = await searchPlayers(q);
      setResults(data.results || []);
    } catch {
      setResults([]);
    } finally {
      setSearching(false);
    }
  }

  const showTrending = query.length < 2 && results.length === 0;

  return (
    <div className="players-page">
      <h1>Players</h1>

      <div className="search-bar">
        <input
          type="text"
          placeholder="Search players..."
          value={query}
          onChange={handleSearch}
          className="search-input"
          autoFocus
        />
      </div>

      {results.length > 0 && (
        <Card>
          <div className="player-results">
            {results.map((p) => (
              <div
                key={p.nhl_id}
                className="player-row"
                onClick={() => navigate(`/players/${p.nhl_id}`)}
              >
                <span className="player-name">{p.name}</span>
                <span className="player-pos">{p.position}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {query.length >= 2 && results.length === 0 && !searching && (
        <p className="empty-state">No players found</p>
      )}

      {showTrending && <TrendingPlayers />}
    </div>
  );
}

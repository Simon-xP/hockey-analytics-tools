const API_BASE = "https://localhost:8000/api";

async function fetchJSON(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export function getTodayGames() {
  return fetchJSON("/dashboard/today");
}

export function getStandings() {
  return fetchJSON("/dashboard/standings");
}

export function getScheduleOutlook() {
  return fetchJSON("/dashboard/schedule-outlook");
}

export function searchPlayers(query) {
  return fetchJSON(`/players/search?q=${encodeURIComponent(query)}`);
}

export function getPlayer(nhlId) {
  return fetchJSON(`/players/${nhlId}`);
}

export function getPlayerForecast(nhlId, gameDate) {
  const params = gameDate ? `?game_date=${gameDate}` : "";
  return fetchJSON(`/players/${nhlId}/forecast${params}`);
}

export function getRegressionCandidates() {
  return fetchJSON("/dashboard/regression");
}

export function getOptimalAdds(limit = 50) {
  return fetchJSON(`/dashboard/optimal-adds?limit=${limit}`);
}

// Yahoo Fantasy
export function getYahooStatus() {
  return fetchJSON("/yahoo/status");
}

export function getYahooAuthUrl() {
  return fetchJSON("/yahoo/connect");
}

export function yahooCallback(code) {
  return fetchJSON(`/yahoo/callback?code=${encodeURIComponent(code)}`);
}

export function getYahooLeagues() {
  return fetchJSON("/yahoo/leagues");
}

export function getYahooTeam(leagueKey) {
  return fetchJSON(`/yahoo/team/${leagueKey}`);
}

export function getYahooStandings(leagueKey) {
  return fetchJSON(`/yahoo/standings/${leagueKey}`);
}

export function getYahooFreeAgents(leagueKey, count = 25) {
  return fetchJSON(`/yahoo/free-agents/${leagueKey}?count=${count}`);
}

export function getYahooOptimalAdds(leagueKey, count = 50) {
  return fetchJSON(`/yahoo/optimal-adds/${leagueKey}?count=${count}`);
}

export function getYahooTrending(leagueKey, count = 20) {
  return fetchJSON(`/yahoo/trending/${leagueKey}?count=${count}`);
}

// News & Daily Faceoff
export function getGoalieStarts(date) {
  const params = date ? `?date=${date}` : "";
  return fetchJSON(`/news/goalie-starts${params}`);
}

export function getStreamableGoalies(date) {
  const params = date ? `?date=${date}` : "";
  return fetchJSON(`/news/streamable-goalies${params}`);
}

export function getTeamLines(teamSlug) {
  return fetchJSON(`/news/lines/${teamSlug}`);
}

export function getNewsFeed(limit = 20, offset = 0) {
  return fetchJSON(`/news/feed?limit=${limit}&offset=${offset}`);
}

export function getTeamInjuries(teamSlug) {
  return fetchJSON(`/news/injuries/${teamSlug}`);
}

export function getAllInjuries() {
  return fetchJSON(`/news/injuries`);
}

// Goalie matchups
export function getGoalieMatchupRankings() {
  return fetchJSON("/goalie-matchups/rankings");
}

export function getGoalieMatchupTeam(abbrev) {
  return fetchJSON(`/goalie-matchups/team/${abbrev}`);
}

export function getYahooRosterWeek(leagueKey) {
  return fetchJSON(`/yahoo/roster-week/${leagueKey}`);
}

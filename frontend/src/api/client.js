const API_BASE = "https://localhost:8000/api";

// Client-side cache: stores responses for 10 minutes
const cache = new Map();
const CACHE_TTL = 10 * 60 * 1000;

async function fetchJSON(path, { skipCache = false } = {}) {
  const now = Date.now();

  if (!skipCache && cache.has(path)) {
    const { data, timestamp } = cache.get(path);
    if (now - timestamp < CACHE_TTL) {
      return data;
    }
    cache.delete(path);
  }

  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  const data = await res.json();

  cache.set(path, { data, timestamp: now });
  return data;
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
  return fetchJSON(`/players/search?q=${encodeURIComponent(query)}`, { skipCache: true });
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

export function getNewsFeed(limit = 20) {
  return fetchJSON(`/news/feed?limit=${limit}`);
}

export function getTeamInjuries(teamSlug) {
  return fetchJSON(`/news/injuries/${teamSlug}`);
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

// Clear all cached data — call when user wants fresh data
export function clearCache() {
  cache.clear();
}

// Prefetch all dashboard + Yahoo data on app startup so tabs load instantly
export async function prefetchAll() {
  try {
    // Start everything in parallel
    const statusPromise = getYahooStatus();
    const todayPromise = getTodayGames();
    const schedulePromise = getScheduleOutlook(7);
    const regressionPromise = getRegressionCandidates();

    const [status] = await Promise.all([
      statusPromise,
      todayPromise,
      schedulePromise,
      regressionPromise,
    ]);

    // If Yahoo is connected, prefetch Yahoo-specific data
    if (status?.connected) {
      const leagues = await getYahooLeagues();
      const leagueKey = leagues?.leagues?.[0]?.league_key;
      if (leagueKey) {
        // Fire these in parallel
        await Promise.all([
          getYahooOptimalAdds(leagueKey, 50),
          getYahooRosterWeek(leagueKey),
          getYahooTrending(leagueKey, 20),
        ]);
      }
    } else {
      // Prefetch non-Yahoo optimal adds
      await getOptimalAdds(50);
    }
  } catch {
    // Prefetch failures are fine — pages will fetch on demand
  }
}

import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Players from "./pages/Players";
import PlayerDetail from "./pages/PlayerDetail";
import Roster from "./pages/Roster";
import OptimalAdds from "./pages/OptimalAdds";
import TradeTargets from "./pages/TradeTargets";
import GoalieMatchups from "./pages/GoalieMatchups";
import StreamableGoalies from "./pages/StreamableGoalies";
import Injuries from "./pages/Injuries";
import Agent from "./pages/Agent";
import Fleet from "./pages/Fleet";
import BuildLog from "./pages/BuildLog";
import { AgentProvider } from "./state/agentStore";
import {
  getTodayGames,
  getScheduleOutlook,
  getRegressionCandidates,
  getYahooStatus,
} from "./api/client";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10 * 60 * 1000,
      gcTime: 30 * 60 * 1000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

export default function App() {
  useEffect(() => {
    queryClient.prefetchQuery({ queryKey: ["today-games"], queryFn: getTodayGames });
    queryClient.prefetchQuery({ queryKey: ["schedule-outlook"], queryFn: getScheduleOutlook });
    queryClient.prefetchQuery({ queryKey: ["regression"], queryFn: getRegressionCandidates });
    queryClient.prefetchQuery({ queryKey: ["yahoo-status"], queryFn: getYahooStatus });
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <AgentProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Layout />}>
              {/* Agent monitoring — the primary surface */}
              <Route index element={<Fleet />} />
              <Route path="league/:leagueId" element={<Agent />} />

              {/* Legacy manual tools */}
              <Route path="dashboard" element={<Dashboard />} />
              <Route path="players" element={<Players />} />
              <Route path="players/:nhlId" element={<PlayerDetail />} />
              <Route path="roster" element={<Roster />} />
              <Route path="adds" element={<OptimalAdds />} />
              <Route path="trades" element={<TradeTargets />} />
              <Route path="goalie-matchups" element={<GoalieMatchups />} />
              <Route path="streamable-goalies" element={<StreamableGoalies />} />
              <Route path="injuries" element={<Injuries />} />

              {/* Project write-up, rendered from docs/how-i-made-this.md */}
              <Route path="how-i-made-this" element={<BuildLog />} />

              <Route path="agent" element={<Navigate to="/" replace />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AgentProvider>
      <ReactQueryDevtools initialIsOpen={false} />
    </QueryClientProvider>
  );
}

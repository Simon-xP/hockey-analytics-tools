import { useEffect } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
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
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="players" element={<Players />} />
            <Route path="players/:nhlId" element={<PlayerDetail />} />
            <Route path="roster" element={<Roster />} />
            <Route path="adds" element={<OptimalAdds />} />
            <Route path="trades" element={<TradeTargets />} />
            <Route path="goalie-matchups" element={<GoalieMatchups />} />
            <Route path="streamable-goalies" element={<StreamableGoalies />} />
            <Route path="injuries" element={<Injuries />} />
            <Route path="agent" element={<Agent />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <ReactQueryDevtools initialIsOpen={false} />
    </QueryClientProvider>
  );
}

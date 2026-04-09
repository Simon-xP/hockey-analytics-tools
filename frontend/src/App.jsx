import { useEffect } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Players from "./pages/Players";
import PlayerDetail from "./pages/PlayerDetail";
import Roster from "./pages/Roster";
import OptimalAdds from "./pages/OptimalAdds";
import TradeTargets from "./pages/TradeTargets";
import { prefetchAll } from "./api/client";

export default function App() {
  useEffect(() => {
    prefetchAll();
  }, []);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="players" element={<Players />} />
          <Route path="players/:nhlId" element={<PlayerDetail />} />
          <Route path="roster" element={<Roster />} />
          <Route path="adds" element={<OptimalAdds />} />
          <Route path="trades" element={<TradeTargets />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

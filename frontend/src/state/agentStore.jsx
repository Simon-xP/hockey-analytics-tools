// In-memory store for agent control state.
//
// Everything here mutates the MOCK data in src/mock/agentData.js so the pause /
// stop / approve / override controls do something visible while the agent loop is
// unbuilt. Nothing persists across a reload. When the real agent lands, each
// setter becomes a mutation against /api/agent/* and this file keeps its shape.

import { useCallback, useMemo, useState } from "react";
import { AgentContext } from "./agentContext";
import { LEAGUES } from "../mock/agentData";

export function AgentProvider({ children }) {
  const [leagues, setLeagues] = useState(LEAGUES);

  const patchLeague = useCallback((leagueId, patch) => {
    setLeagues((prev) =>
      prev.map((l) => (l.id === leagueId ? { ...l, ...(typeof patch === "function" ? patch(l) : patch) } : l))
    );
  }, []);

  const value = useMemo(() => {
    return {
      leagues,

      getLeague: (id) => leagues.find((l) => l.id === id),

      pause: (id, note = "Paused from the monitor.") =>
        patchLeague(id, { status: "PAUSED", pausedBy: "you", pausedAt: "just now", pauseNote: note, nextRun: "paused" }),

      resume: (id) =>
        patchLeague(id, {
          status: "ACTIVE",
          pausedBy: null,
          pausedAt: null,
          pauseNote: null,
          stoppedReason: null,
          stoppedAt: null,
          nextRun: "in 2m",
        }),

      stop: (id, reason = "Kill switch pulled from the monitor.") =>
        patchLeague(id, { status: "STOPPED", stoppedReason: reason, stoppedAt: "just now", nextRun: "stopped" }),

      // Aggression and the move cap are deliberately read-only: they come out of
      // the model and the safety config, and are changed in code, not here.
      setMode: (id, mode) => patchLeague(id, { mode }),

      setMoveStatus: (id, moveId, status) =>
        patchLeague(id, (l) => ({
          plan: {
            ...l.plan,
            moves: l.plan.moves.map((m) => (m.id === moveId ? { ...m, status } : m)),
          },
        })),

      togglePlayerTag: (id, playerName, tag) =>
        patchLeague(id, (l) => ({
          roster: l.roster.map((p) => (p.name === playerName ? { ...p, tag: p.tag === tag ? null : tag } : p)),
        })),

      pauseAll: () =>
        setLeagues((prev) =>
          prev.map((l) =>
            l.status === "ACTIVE"
              ? { ...l, status: "PAUSED", pausedBy: "you", pausedAt: "just now", pauseNote: "Fleet-wide pause.", nextRun: "paused" }
              : l
          )
        ),

      resumeAll: () =>
        setLeagues((prev) =>
          prev.map((l) =>
            l.status === "PAUSED"
              ? { ...l, status: "ACTIVE", pausedBy: null, pausedAt: null, pauseNote: null, nextRun: "in 2m" }
              : l
          )
        ),
    };
  }, [leagues, patchLeague]);

  return <AgentContext.Provider value={value}>{children}</AgentContext.Provider>;
}

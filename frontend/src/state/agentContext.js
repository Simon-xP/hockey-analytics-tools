// Context + hook for the agent control store. Kept separate from the provider
// component so fast refresh stays happy (a module may export either components
// or plain values, not both).
import { createContext, useContext } from "react";

export const AgentContext = createContext(null);

export function useAgentStore() {
  const ctx = useContext(AgentContext);
  if (!ctx) throw new Error("useAgentStore must be used inside <AgentProvider>");
  return ctx;
}

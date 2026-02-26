/**
 * Configuration store
 * Manages authentication, agent selection, and app settings
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api } from '@/lib/api';

export interface AgentInfo {
  agent_id: string;
  name?: string;
  description?: string;
  status?: string;
  created_at?: string;
  is_public?: boolean;
  created_by?: string;
}

interface ConfigState {
  // Auth state
  isLoggedIn: boolean;
  userId: string;

  // Agent state
  agentId: string;
  agents: AgentInfo[];

  // Actions
  login: (userId: string) => void;
  logout: () => void;
  setAgentId: (id: string) => void;
  setAgents: (agents: AgentInfo[]) => void;
  refreshAgents: () => Promise<void>;
}

export const useConfigStore = create<ConfigState>()(
  persist(
    (set, get) => ({
      // Default values
      isLoggedIn: false,
      userId: '',
      agentId: '',
      agents: [],

      // Actions
      login: (userId) => set({ isLoggedIn: true, userId }),

      logout: () => set({
        isLoggedIn: false,
        userId: '',
        agentId: '',
        agents: [],
      }),

      setAgentId: (id) => set({ agentId: id }),

      setAgents: (agents) => set({ agents }),

      refreshAgents: async () => {
        const { userId } = get();
        if (!userId) return;
        try {
          const res = await api.getAgents(userId);
          if (res.success) {
            set({ agents: res.agents });
          }
        } catch (err) {
          console.error('Failed to refresh agents:', err);
        }
      },
    }),
    {
      name: 'narra-nexus-config',
    }
  )
);

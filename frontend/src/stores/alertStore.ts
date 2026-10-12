import { create } from 'zustand';
import type { WhaleAlert } from '@/types/whale';

interface AlertStoreState {
  alerts: WhaleAlert[];
  addAlert: (alert: WhaleAlert) => void;
  clearAlerts: () => void;
}

export const useAlertStore = create<AlertStoreState>()((set) => ({
  alerts: [],
  addAlert: (alert) =>
    set((state) => ({
      alerts: [alert, ...state.alerts].slice(0, 100),
    })),
  clearAlerts: () => set({ alerts: [] }),
}));

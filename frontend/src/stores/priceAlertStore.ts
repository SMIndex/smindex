import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface PriceAlert {
  id: string;
  marketId: number;
  symbol: string;
  targetPrice: number;
  direction: 'above' | 'below';
  triggered: boolean;
  createdAt: number;
}

interface PriceAlertStoreState {
  alerts: PriceAlert[];
  addAlert: (alert: Omit<PriceAlert, 'id' | 'triggered' | 'createdAt'>) => void;
  removeAlert: (id: string) => void;
  markTriggered: (id: string) => void;
}

export const usePriceAlertStore = create<PriceAlertStoreState>()(
  persist(
    (set) => ({
      alerts: [],
      addAlert: (alert) =>
        set((s) => ({
          alerts: [
            ...s.alerts,
            {
              ...alert,
              id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
              triggered: false,
              createdAt: Date.now(),
            },
          ],
        })),
      removeAlert: (id) => set((s) => ({ alerts: s.alerts.filter((a) => a.id !== id) })),
      markTriggered: (id) =>
        set((s) => ({
          alerts: s.alerts.map((a) => (a.id === id ? { ...a, triggered: true } : a)),
        })),
    }),
    { name: 'perpl-price-alerts' },
  ),
);

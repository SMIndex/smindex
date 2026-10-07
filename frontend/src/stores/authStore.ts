import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface AuthUser {
  wallet_address: string;
  perpl_linked: boolean;
  username?: string | null;
}

interface AuthState {
  token: string | null;
  user: AuthUser | null;
  isAuthenticated: boolean;
  _hydrated: boolean;
  setAuth: (token: string, user: AuthUser) => void;
  clearAuth: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      isAuthenticated: false,
      _hydrated: false,
      setAuth: (token, user) =>
        set({ token, user, isAuthenticated: true }),
      clearAuth: () =>
        set({ token: null, user: null, isAuthenticated: false }),
    }),
    {
      name: 'perpl-auth',
      partialize: (state) => ({
        token: state.token,
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
      onRehydrateStorage: () => (state, error) => {
        if (error) {
          // Corrupted localStorage — clear and continue
          try { localStorage.removeItem('perpl-auth'); } catch {}
          if (state) {
            state.token = null;
            state.user = null;
            state.isAuthenticated = false;
            state._hydrated = true;
          }
          return;
        }
        if (state) {
          state.isAuthenticated = !!state.token;
          state._hydrated = true;
        }
      },
    },
  ),
);

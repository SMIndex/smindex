import { create } from 'zustand';

const STORAGE_KEY = 'perpl_sound_enabled';

interface SoundStore {
  enabled: boolean;
  toggle: () => void;
}

export const useSoundStore = create<SoundStore>()((set) => ({
  enabled: localStorage.getItem(STORAGE_KEY) !== 'false',
  toggle: () =>
    set((state) => {
      const enabled = !state.enabled;
      localStorage.setItem(STORAGE_KEY, String(enabled));
      return { enabled };
    }),
}));

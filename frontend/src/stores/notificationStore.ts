import { create } from 'zustand';

export type NotificationType = 'sl_tp_triggered' | 'copy_updates' | 'whale_alerts';

export interface Notification {
  id: string;
  type: NotificationType;
  message: string;
  timestamp: number;
  read: boolean;
}

interface NotificationStore {
  notifications: Notification[];
  unreadCount: number;
  addNotification: (type: NotificationType, message: string) => void;
  markAllRead: () => void;
  clearAll: () => void;
}

const MAX_NOTIFICATIONS = 50;
const STORAGE_KEY = 'perpl_notifications';

function loadFromStorage(): Notification[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const all: Notification[] = JSON.parse(raw);
    // Drop legacy "Leader trade: unknown" rows produced by the pre-fix
    // useWebSocket handler. The actual message text was saved verbatim
    // to localStorage, so the rows can't fix themselves on next render.
    const cleaned = all.filter(
      (n) => !(n.type === 'copy_updates' && /Leader trade: unknown/i.test(n.message)),
    );
    if (cleaned.length !== all.length) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(cleaned));
    }
    return cleaned;
  } catch {
    return [];
  }
}

function saveToStorage(notifications: Notification[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(notifications));
  } catch {}
}

const initial = loadFromStorage();

export const useNotificationStore = create<NotificationStore>()((set) => ({
  notifications: initial,
  unreadCount: initial.filter((n) => !n.read).length,
  addNotification: (type, message) =>
    set((state) => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const notification: Notification = { id, type, message, timestamp: Date.now(), read: false };
      const notifications = [notification, ...state.notifications].slice(0, MAX_NOTIFICATIONS);
      saveToStorage(notifications);
      return { notifications, unreadCount: notifications.filter((n) => !n.read).length };
    }),
  markAllRead: () =>
    set((state) => {
      const notifications = state.notifications.map((n) => ({ ...n, read: true }));
      saveToStorage(notifications);
      return { notifications, unreadCount: 0 };
    }),
  clearAll: () => {
    saveToStorage([]);
    return set({ notifications: [], unreadCount: 0 });
  },
}));

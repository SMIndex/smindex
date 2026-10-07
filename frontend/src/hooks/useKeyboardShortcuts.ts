import { useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';

const SHORTCUTS: Record<string, string> = {
  t: '/trade',
  p: '/portfolio',
  c: '/copy',
  s: '/social',
};

export function useKeyboardShortcuts(onToggleHelp?: () => void) {
  const navigate = useNavigate();

  const handleKey = useCallback(
    (e: KeyboardEvent) => {
      // Ignore when typing in inputs
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if ((e.target as HTMLElement)?.isContentEditable) return;

      // Ctrl+/ or Ctrl+? — toggle shortcut help
      if ((e.ctrlKey || e.metaKey) && e.key === '/') {
        e.preventDefault();
        onToggleHelp?.();
        return;
      }

      // Escape — close any open modal (dispatch custom event)
      if (e.key === 'Escape') {
        window.dispatchEvent(new CustomEvent('close_modals'));
        return;
      }

      // Navigation shortcuts (single key, no modifiers)
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const path = SHORTCUTS[e.key.toLowerCase()];
      if (path) navigate(path);
    },
    [navigate, onToggleHelp],
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [handleKey]);
}

export const SHORTCUT_LIST = [
  { key: 'T', description: 'Go to Trade' },
  { key: 'P', description: 'Go to Portfolio' },
  { key: 'C', description: 'Go to Copy Trade' },
  { key: 'S', description: 'Go to Social' },
  { key: 'Esc', description: 'Close modal / panel' },
  { key: 'Ctrl + /', description: 'Show keyboard shortcuts' },
];

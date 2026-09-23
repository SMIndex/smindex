import { useState, useEffect, useCallback } from 'react';

type Theme = 'dark' | 'light';

const STORAGE_KEY = 'perpl-theme';

function getInitialTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === 'light' || stored === 'dark') return stored;
  return 'dark';
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'light') {
      root.classList.add('light');
    } else {
      root.classList.remove('light');
    }
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setThemeState((prev) => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
  }, []);

  return { theme, toggleTheme, setTheme, isDark: theme === 'dark' };
}

/**
 * Get current chart colors based on theme.
 * Used by TradingChart and other chart components.
 */
export function getChartColors() {
  const style = getComputedStyle(document.documentElement);
  return {
    bg: style.getPropertyValue('--chart-bg').trim() || '#0d1117',
    grid: style.getPropertyValue('--chart-grid').trim() || '#161b22',
    border: style.getPropertyValue('--chart-border').trim() || '#1c2128',
    text: style.getPropertyValue('--chart-text').trim() || '#8b949e',
  };
}

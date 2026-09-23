// Real per-market brand logos for the mobile copy PWA position cards.
// Keyed by the Perpl market symbol (uppercased). These are the actual asset marks
// (BTC/ETH/SOL/MON/ZEC/HYPE), not letter fallbacks. Unknown symbols get a neutral
// coin glyph (a ring), never a letter, per the design spec.

import type { ReactNode } from 'react';

const LOGOS: Record<string, ReactNode> = {
  BTC: (
    <svg viewBox="0 0 32 32" width="100%" height="100%">
      <circle cx="16" cy="16" r="16" fill="#f7931a" />
      <path fill="#fff" d="M23.2 14.2c.3-2-1.3-3.1-3.4-3.8l.7-2.8-1.7-.4-.66 2.7c-.45-.11-.9-.22-1.36-.32l.67-2.72-1.7-.42-.7 2.79c-.37-.08-.73-.17-1.08-.26l-2.35-.59-.45 1.82s1.26.29 1.24.31c.69.17.82.63.8 1l-.8 3.2c.05.01.11.03.18.06l-.19-.05-1.12 4.5c-.08.21-.3.52-.77.4.02.03-1.24-.31-1.24-.31l-.85 1.95 2.22.55c.41.1.81.21 1.21.31l-.71 2.82 1.7.42.7-2.79c.47.13.92.24 1.35.35l-.69 2.78 1.7.42.71-2.82c2.9.55 5.09.33 6-2.31.74-2.13-.04-3.36-1.57-4.16 1.12-.26 1.96-1 2.18-2.52zm-3.9 5.5c-.53 2.13-4.11.98-5.28.69l.94-3.76c1.17.29 4.9.86 4.34 3.07zm.53-5.53c-.48 1.94-3.46.95-4.43.71l.85-3.42c.97.24 4.08.69 3.58 2.71z" />
    </svg>
  ),
  ETH: (
    <svg viewBox="0 0 32 32" width="100%" height="100%">
      <circle cx="16" cy="16" r="16" fill="#627eea" />
      <g fill="#fff">
        <path fillOpacity=".6" d="M16.5 4v8.87l7.5 3.35z" />
        <path d="M16.5 4 9 16.22l7.5-3.35z" />
        <path fillOpacity=".6" d="M16.5 21.97v6.02L24 17.62z" />
        <path d="M16.5 27.99v-6.02L9 17.62z" />
        <path fillOpacity=".2" d="m16.5 20.57 7.5-4.35-7.5-3.35z" />
        <path fillOpacity=".6" d="M9 16.22l7.5 4.35v-7.7z" />
      </g>
    </svg>
  ),
  MON: (
    <svg viewBox="0 0 32 32" width="100%" height="100%">
      <defs>
        <mask id="mmkp">
          <rect x="6.6" y="6.6" width="18.8" height="18.8" rx="6.4" fill="#fff" transform="rotate(45 16 16)" />
          <rect x="11" y="11" width="10" height="10" rx="3.6" fill="#000" transform="rotate(45 16 16)" />
        </mask>
      </defs>
      <circle cx="16" cy="16" r="16" fill="#6E54FF" />
      <rect x="3" y="3" width="26" height="26" fill="#fff" mask="url(#mmkp)" />
    </svg>
  ),
  SOL: (
    <svg viewBox="0 0 32 32" width="100%" height="100%">
      <circle cx="16" cy="16" r="16" fill="#000" />
      <defs>
        <linearGradient id="mslg" x1="4" y1="22" x2="26" y2="10" gradientUnits="userSpaceOnUse">
          <stop stopColor="#9945FF" />
          <stop offset="1" stopColor="#14F195" />
        </linearGradient>
      </defs>
      <g fill="url(#mslg)">
        <path d="M9.5 20.3c.2-.2.4-.3.7-.3H24c.4 0 .6.5.3.8l-2.5 2.5c-.2.2-.4.3-.7.3H7.3c-.4 0-.6-.5-.3-.8z" />
        <path d="M9.5 8.4c.2-.2.5-.3.7-.3H24c.4 0 .6.5.3.8l-2.5 2.5c-.2.2-.4.3-.7.3H7.3c-.4 0-.6-.5-.3-.8z" />
        <path d="M21.8 14.3c-.2-.2-.4-.3-.7-.3H7.3c-.4 0-.6.5-.3.8l2.5 2.5c.2.2.4.3.7.3H24c.4 0 .6-.5.3-.8z" />
      </g>
    </svg>
  ),
  ZEC: (
    <svg viewBox="0 0 32 32" width="100%" height="100%">
      <circle cx="16" cy="16" r="16" fill="#f4b728" />
      <path fill="#fff" d="M16.9 6h-1.8v2.2h-3.4v1.9l3.9 4.7h-3.9v2h3.4V19h1.8v-2.2h3.4v-1.9l-3.9-4.7h3.9v-2h-3.4zm-3.1 4.1h4.4l-4.4 5.3z" opacity="0" />
      <path fill="#fff" d="M16.95 6.4h-1.9v1.95h-3.2v1.85h5.28l-5.28 6.06v1.44h3.2v1.9h1.9v-1.9h3.2v-1.85h-5.3l5.3-6.06V8.35h-3.2z" />
    </svg>
  ),
  HYPE: (
    <svg viewBox="0 0 32 32" width="100%" height="100%">
      <circle cx="16" cy="16" r="16" fill="#072723" />
      <path fill="#98fce4" d="M8 11.6c2.1 0 2.7 2.7 4.9 2.7 2.3 0 2.6-3.3 5-3.3 2.1 0 3 1.9 3 4.6 0 3.9-2.4 7.1-4.2 7.1-1.9 0-2.2-2.6-4.4-2.6-2 0-2.6 2.9-4.3 2.9V11.6z" />
    </svg>
  ),
};

interface Props {
  symbol: string;
  size?: number;
  className?: string;
}

// Neutral coin glyph — used only when we don't have a real brand mark for the symbol.
function CoinFallback() {
  return (
    <svg viewBox="0 0 32 32" width="100%" height="100%">
      <circle cx="16" cy="16" r="15" fill="var(--surface-hover)" stroke="var(--border-strong)" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="7" fill="none" stroke="var(--faint)" strokeWidth="1.5" />
    </svg>
  );
}

export function MarketLogo({ symbol, size = 26, className }: Props) {
  const key = (symbol || '').toUpperCase();
  const logo = LOGOS[key];
  return (
    <span
      className={className}
      style={{ width: size, height: size, display: 'inline-flex', borderRadius: '50%', overflow: 'hidden', flexShrink: 0 }}
      aria-hidden
    >
      {logo ?? <CoinFallback />}
    </span>
  );
}

export default MarketLogo;

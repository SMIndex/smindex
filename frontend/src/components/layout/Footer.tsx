// Global footer for document-style routes (not the fixed /trade terminal).
// Subtle top border, Perpl candlestick mark, theme-consistent product copy.
export default function Footer() {
  return (
    <footer className="rd-sans relative z-[1] mt-6 px-4 md:px-6 py-5 border-t" style={{ borderColor: 'var(--border)' }}>
      <div className="max-w-[1600px] flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <svg width="16" height="16" viewBox="0 0 200 200" fill="none" aria-hidden>
            <rect x="24" y="30" width="34" height="150" rx="11" fill="var(--accent)" />
            <rect x="84" y="12" width="34" height="96" rx="11" fill="var(--accent-2)" />
            <rect x="144" y="74" width="34" height="110" rx="11" fill="var(--accent)" />
          </svg>
          <span className="rd-mono text-[12px] font-semibold" style={{ color: 'var(--text)' }}>SMINDEX</span>
          <span className="text-[12px]" style={{ color: 'var(--faint)' }}>· settled on Monad</span>
        </div>
        <div className="rd-mono text-[11px]" style={{ color: 'var(--faint)' }}>
          Perpetual futures · Monad Mainnet
        </div>
      </div>
    </footer>
  );
}

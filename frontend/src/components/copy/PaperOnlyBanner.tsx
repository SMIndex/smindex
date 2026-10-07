// Persistent reminder shown across all copy v1 screens.
// Copy is simulated — no real Perpl orders are ever placed in v1.
export default function PaperOnlyBanner() {
  return (
    <div className="flex items-center gap-2.5 px-4 py-2.5 rounded-lg bg-warning/10 border border-warning/25 text-xs text-text-secondary">
      <svg className="w-4 h-4 shrink-0 text-warning" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01M5.07 19h13.86c1.54 0 2.5-1.67 1.73-3L13.73 4a2 2 0 00-3.46 0L3.34 16c-.77 1.33.19 3 1.73 3z" />
      </svg>
      <span>
        <span className="font-semibold text-text-primary">Paper copy only</span>
        {' — no real Perpl orders are placed. All positions and PnL below are simulated.'}
      </span>
    </div>
  );
}

export default function HeatmapLegend() {
  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-text-primary mb-3">Legend</h3>
      <div className="flex flex-wrap items-center gap-6 text-xs">
        {/* Intensity gradient */}
        <div className="flex items-center gap-2">
          <span className="text-text-secondary">Intensity:</span>
          <div className="flex items-center gap-0.5">
            <span className="text-text-secondary">Low</span>
            <div
              className="w-24 h-3 rounded-sm"
              style={{
                background:
                  'linear-gradient(to right, #1a1a2e, #16213e, #0f3460, #533483, #e94560)',
              }}
            />
            <span className="text-text-secondary">High</span>
          </div>
        </div>

        {/* Long liq */}
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-sm bg-danger/70" />
          <span className="text-text-secondary">Long Liquidations</span>
        </div>

        {/* Short liq */}
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-sm bg-success/70" />
          <span className="text-text-secondary">Short Liquidations</span>
        </div>

        {/* Current price */}
        <div className="flex items-center gap-2">
          <div className="w-5 h-0 border-t-2 border-dashed border-accent" />
          <span className="text-text-secondary">Current Price</span>
        </div>
      </div>
    </div>
  );
}

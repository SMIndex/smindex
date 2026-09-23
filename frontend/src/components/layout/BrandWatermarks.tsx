// Embossed Monad (top-right) + Perpl (bottom-left) background watermarks + accent glow.
// Fixed, non-interactive, behind all content. Emboss treatment via CSS (.rd-wm-*).
// glow=false (e.g. on the dense /trade terminal) renders only the cheap static
// embossed marks — no blurred accent glow layer that would repaint behind the chart.
export default function BrandWatermarks({ glow = true }: { glow?: boolean }) {
  return (
    <>
      {glow && <div className="rd-glow" aria-hidden />}
      <div className="rd-watermarks" aria-hidden>
        {/* Monad — rounded-diamond with rounded-diamond knockout */}
        <svg className="rd-wm-monad" viewBox="0 0 480 480" fill="none">
          <path
            fill="currentColor"
            d="M240.135 90C196.78 90 90 196.68 90 240C90 283.318 196.78 390 240.135 390C283.491 390 390.273 283.316 390.273 240C390.273 196.682 283.493 90 240.135 90ZM216.739 325.774C198.457 320.796 149.302 234.89 154.285 216.624C159.268 198.357 245.251 149.248 263.533 154.226C281.817 159.204 330.971 245.108 325.989 263.376C321.005 281.642 235.023 330.752 216.739 325.774Z"
          />
        </svg>
        {/* Perpl — three staggered vertical bars (candlestick stagger) */}
        <svg className="rd-wm-perpl" viewBox="0 0 200 200" fill="none">
          <rect x="24" y="30" width="34" height="150" rx="12" fill="currentColor" />
          <rect x="84" y="12" width="34" height="96" rx="12" fill="currentColor" />
          <rect x="144" y="74" width="34" height="110" rx="12" fill="currentColor" />
        </svg>
      </div>
    </>
  );
}

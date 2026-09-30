// SMINDEX brand lockup and mark.
//
// The lockups are served as static SVGs from /brand/ with the wordmark already
// converted to outlines (scripts/build_brand_assets.py), so they render
// identically with no webfont loaded — an <img> cannot use the page's fonts,
// which is exactly why the text had to be outlined.
//
// Sizing: the lockup is 340x72, the mark 64x64. Both are given an explicit
// height and `width: auto` so they never distort, and an alt/aria label so the
// brand is still announced to a screen reader.

type Theme = 'light' | 'dark';

const LOCKUP_RATIO = 340 / 72;

/** Full lockup — mark + outlined SMINDEX wordmark. Follows the shell theme. */
export function BrandLockup({ theme, height = 26, className }: {
  theme: Theme;
  height?: number;
  className?: string;
}) {
  return (
    <img
      src={`/brand/smindex-logo-${theme}.svg`}
      alt="SMINDEX"
      className={className}
      width={Math.round(height * LOCKUP_RATIO)}
      height={height}
      style={{ height, width: 'auto', display: 'block' }}
      draggable={false}
    />
  );
}

/** Mark alone — used where the lockup would be too wide to read. */
export function BrandMark({ size = 24, className }: {
  size?: number;
  className?: string;
}) {
  return (
    <img
      src="/brand/smindex-mark.svg"
      alt="SMINDEX"
      className={className}
      width={size}
      height={size}
      style={{ width: size, height: size, display: 'block' }}
      draggable={false}
    />
  );
}

/**
 * Brand slot for the shells: the lockup normally, the mark alone once the
 * available width drops below what the wordmark needs to stay legible
 * (collapsed sidebar, narrow mobile bar).
 */
export function Brand({ theme, compact = false, height = 26 }: {
  theme: Theme;
  compact?: boolean;
  height?: number;
}) {
  return compact
    ? <BrandMark size={height + 2} />
    : <BrandLockup theme={theme} height={height} />;
}

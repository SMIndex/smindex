import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

// Filter / sort controls on phones (spec 4.3): at <= 820px the children move into
// a bottom sheet opened by one 44px button that shows the current choice; on
// wider screens they render inline exactly as before. Same state, same handlers.

const PHONE = '(max-width: 820px)';

export function usePhone(): boolean {
  const [on, setOn] = useState(() => typeof window !== 'undefined' && window.matchMedia(PHONE).matches);
  useEffect(() => {
    const m = window.matchMedia(PHONE);
    const f = (e: MediaQueryListEvent) => setOn(e.matches);
    setOn(m.matches);
    m.addEventListener('change', f);
    return () => m.removeEventListener('change', f);
  }, []);
  return on;
}

export default function MobileSheetB({ label, summary, title, children }: {
  label: string; summary?: string; title?: string; children: ReactNode;
}) {
  const phone = usePhone();
  const [open, setOpen] = useState(false);
  useEffect(() => { if (!phone) setOpen(false); }, [phone]);
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [open]);
  if (!phone) return <>{children}</>;
  const root = document.querySelector('.dsb') ?? document.body;
  return (
    <>
      <button className="msheet-btn" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="M4 6h16M7 12h10M10 18h4" /></svg>
        <span>{label}</span>
        {summary && <span className="msheet-sum">{summary}</span>}
      </button>
      {open && createPortal(
        <div className="msheet-back" onClick={() => setOpen(false)}>
          <div className="msheet" role="dialog" aria-modal="true" aria-label={title ?? label} onClick={(e) => e.stopPropagation()}>
            <div className="msheet-grip" aria-hidden />
            <div className="msheet-head">
              <b>{title ?? label}</b>
              <button className="btn primary" onClick={() => setOpen(false)}>Done</button>
            </div>
            <div className="msheet-body">{children}</div>
          </div>
        </div>,
        root,
      )}
    </>
  );
}

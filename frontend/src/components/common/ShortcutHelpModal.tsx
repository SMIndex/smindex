import { useEffect, useRef } from 'react';
import { SHORTCUT_LIST } from '@/hooks/useKeyboardShortcuts';

export default function ShortcutHelpModal({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handle = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const handleCloseEvent = () => onClose();
    window.addEventListener('keydown', handle);
    window.addEventListener('close_modals', handleCloseEvent);
    return () => {
      window.removeEventListener('keydown', handle);
      window.removeEventListener('close_modals', handleCloseEvent);
    };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 backdrop-blur-sm animate-fadeIn">
      <div
        ref={ref}
        className="bg-bg-card border border-text-secondary/15 rounded-xl shadow-2xl shadow-black/40 w-80 animate-fadeInScale"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-text-secondary/10">
          <h3 className="text-sm font-semibold text-text-primary">Keyboard Shortcuts</h3>
          <button onClick={onClose} className="text-text-secondary hover:text-text-primary transition-colors">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="px-4 py-3 space-y-2">
          {SHORTCUT_LIST.map((s) => (
            <div key={s.key} className="flex items-center justify-between">
              <span className="text-xs text-text-secondary">{s.description}</span>
              <kbd className="text-[10px] font-mono bg-bg-secondary border border-text-secondary/20 rounded px-1.5 py-0.5 text-text-primary">
                {s.key}
              </kbd>
            </div>
          ))}
        </div>
        <div className="px-4 py-2.5 border-t border-text-secondary/10">
          <p className="text-[10px] text-text-secondary text-center">Press Esc to close</p>
        </div>
      </div>
    </div>
  );
}

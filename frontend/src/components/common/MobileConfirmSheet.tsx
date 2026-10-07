import { useEffect, useState } from 'react';
import { subscribeConfirm, answerConfirm, closeConfirm } from '@/lib/mobileConfirm';

// The single mobile confirmation sheet (spec 4.4). Mounted once at the app root.
export default function MobileConfirmSheet() {
  const [s, setS] = useState<any>({ phase: 'idle' });
  useEffect(() => subscribeConfirm(setS), []);
  if (s.phase === 'idle') return null;
  const { req } = s;
  const statusText = s.phase === 'submitting' ? 'Submitted — waiting for Perpl…'
    : s.phase === 'confirmed' ? (s.msg || 'Confirmed by Perpl')
    : s.phase === 'failed' ? (s.msg || 'Failed') : '';
  return (
    <div className="fixed inset-0 z-[120] flex items-end justify-center bg-black/50"
         onClick={() => s.phase === 'ask' ? answerConfirm(false) : closeConfirm()}>
      <div role="dialog" aria-modal="true" aria-label={req.title}
           onClick={(e) => e.stopPropagation()}
           className="w-full max-w-lg rounded-t-2xl p-4 space-y-3"
           style={{ background: 'var(--surface, #12202A)', color: 'var(--text, #fff)',
             paddingBottom: 'calc(16px + env(safe-area-inset-bottom))', border: '1px solid var(--border, #274552)' }}>
        <div className="mx-auto h-1 w-10 rounded-full bg-white/20" />
        <div className={req.danger ? 'text-base font-semibold text-red-400' : 'text-base font-semibold'}>{req.title}</div>
        <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border, #274552)' }}>
          {req.rows.map(([k, v]: [string, string]) => (
            <div key={k} className="flex justify-between gap-3 px-3 py-2 text-sm" style={{ borderBottom: '1px solid var(--border, #274552)' }}>
              <span className="opacity-70">{k}</span><span className="font-mono text-right">{v}</span>
            </div>
          ))}
        </div>
        {s.phase !== 'ask' && (
          <div role="status" className={s.phase === 'failed' ? 'text-sm text-red-400' : s.phase === 'confirmed' ? 'text-sm text-green-400' : 'text-sm opacity-80'}>
            {statusText}
          </div>
        )}
        {s.phase === 'ask' ? (
          <div className="grid grid-cols-2 gap-3 pt-1">
            <button onClick={() => answerConfirm(false)} className="min-h-[48px] rounded-xl text-sm font-semibold"
                    style={{ border: '1px solid var(--border, #274552)' }}>Cancel</button>
            <button onClick={() => answerConfirm(true)}
                    className={req.danger ? 'min-h-[48px] rounded-xl text-sm font-semibold bg-red-500 text-white' : 'min-h-[48px] rounded-xl text-sm font-semibold bg-blue-500 text-white'}>
              {req.confirmLabel || 'Confirm'}
            </button>
          </div>
        ) : s.phase !== 'submitting' ? (
          <button onClick={closeConfirm} className="w-full min-h-[48px] rounded-xl text-sm font-semibold"
                  style={{ border: '1px solid var(--border, #274552)' }}>Close</button>
        ) : null}
      </div>
    </div>
  );
}

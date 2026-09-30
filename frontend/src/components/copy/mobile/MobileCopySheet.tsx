import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { clsx } from 'clsx';
import { useMarkets } from '@/hooks/useMarkets';
import { COPY_LIVE_ENABLED } from '@/config/constants';
import { shortenAddress } from '@/lib/formatters';
import MarketLogo from '@/components/copy/mobile/marketLogos';
import type { CreateSubscriptionPayload, CopySubscription } from '@/lib/copyApi';

interface Props {
  traderWallet: string;
  traderName?: string | null;
  onSubmit: (payload: CreateSubscriptionPayload) => Promise<CopySubscription>;
  onSaved: () => void;
  onClose: () => void;
}

interface FormState {
  allocation_usd: string;
  max_leverage: string;
  max_margin_per_trade: string;
  max_daily_loss: string;
  max_total_loss: string;
  slippage_bps: string;
  allowed_markets: number[];
  copy_new_only: boolean;
}

const DEFAULTS: FormState = {
  allocation_usd: '100',
  max_leverage: '5',
  max_margin_per_trade: '50',
  max_daily_loss: '50',
  max_total_loss: '200',
  slippage_bps: '50',
  allowed_markets: [],
  copy_new_only: true,
};

function num(v: string): number {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : NaN;
}

// Same validation contract as the desktop StartPaperCopyModal.
function validate(form: FormState): Record<string, string> {
  const e: Record<string, string> = {};
  const alloc = num(form.allocation_usd);
  const lev = num(form.max_leverage);
  const margin = num(form.max_margin_per_trade);
  const daily = num(form.max_daily_loss);
  const total = num(form.max_total_loss);
  const slip = num(form.slippage_bps);
  if (!(alloc > 0)) e.allocation_usd = 'Must be > 0';
  if (!(lev >= 1 && lev <= 20)) e.max_leverage = '1–20';
  if (!(margin > 0)) e.max_margin_per_trade = 'Must be > 0';
  if (!(daily > 0)) e.max_daily_loss = 'Must be > 0';
  if (!(total > 0)) e.max_total_loss = 'Must be > 0';
  if (!(slip >= 0 && slip <= 500)) e.slippage_bps = '0–500';
  if (form.allowed_markets.length === 0) e.allowed_markets = 'Select at least one market';
  return e;
}

export default function MobileCopySheet({ traderWallet, traderName, onSubmit, onSaved, onClose }: Props) {
  const { activeMarkets, loading: marketsLoading, error: marketsError } = useMarkets();
  const [open, setOpen] = useState(false);
  const closingRef = useRef(false);
  const [form, setForm] = useState<FormState>(DEFAULTS);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  const handleClose = useCallback(() => {
    if (closingRef.current) return;
    closingRef.current = true;
    setOpen(false);
    window.setTimeout(onClose, 320);
  }, [onClose]);

  useEffect(() => {
    const raf = requestAnimationFrame(() => setOpen(true));
    document.body.style.overflow = 'hidden';
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') handleClose(); };
    window.addEventListener('keydown', onEsc);
    return () => {
      cancelAnimationFrame(raf);
      document.body.style.overflow = '';
      window.removeEventListener('keydown', onEsc);
    };
  }, [handleClose]);

  // Default allowed_markets to ALL active markets once the registry loads (never a hardcoded list).
  useEffect(() => {
    if (activeMarkets.length && form.allowed_markets.length === 0) {
      setForm((f) => (f.allowed_markets.length === 0 ? { ...f, allowed_markets: activeMarkets.map((m) => m.market_id) } : f));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeMarkets]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));
  const toggleMarket = (id: number) =>
    setForm((f) => ({
      ...f,
      allowed_markets: f.allowed_markets.includes(id) ? f.allowed_markets.filter((m) => m !== id) : [...f.allowed_markets, id],
    }));

  const handleSubmit = async () => {
    setApiError(null);
    const e = validate(form);
    setErrors(e);
    if (Object.keys(e).length > 0) return;
    const payload: CreateSubscriptionPayload = {
      trader_wallet: traderWallet,
      sizing_mode: 'fixed',
      allocation_usd: num(form.allocation_usd),
      max_leverage: num(form.max_leverage),
      max_margin_per_trade: num(form.max_margin_per_trade),
      max_daily_loss: num(form.max_daily_loss),
      max_total_loss: num(form.max_total_loss),
      slippage_bps: Math.round(num(form.slippage_bps)),
      allowed_markets: form.allowed_markets,
      copy_new_only: form.copy_new_only,
    };
    setSubmitting(true);
    try {
      await onSubmit(payload);
      setOpen(false);
      window.setTimeout(onSaved, 300);
    } catch (err: any) {
      setApiError(err?.response?.data?.detail || err?.message || 'Failed to save copy settings');
    } finally {
      setSubmitting(false);
    }
  };

  const field = (label: string, key: keyof FormState, suffix: string, hint?: string) => (
    <div className="mc-fld">
      <div className="fl">{label}</div>
      <div className="mc-inp">
        <input
          inputMode="decimal"
          value={form[key] as string}
          onChange={(ev) => set(key, ev.target.value as any)}
          className={clsx(errors[key] && 'err')}
        />
        <span className="sfx">{suffix}</span>
      </div>
      {errors[key] ? <div className="hint err">{errors[key]}</div> : hint ? <div className="hint">{hint}</div> : null}
    </div>
  );

  // Portal to <body> so the fixed sheet + backdrop cover the viewport instead of being
  // clipped inside the app's scrolling, transformed <main>.
  return createPortal(
    <>
      <div className={clsx('mc-sheet-back', open && 'open')} onClick={handleClose} />
      <div className={clsx('mc-copysheet', open && 'open')} role="dialog" aria-modal="true" aria-label="Set up copy">
        <div className="mc-handle" onClick={handleClose} />
        <div className="mc-cs-head">
          <span className="ttl">Set up Copy</span>
          <button className="mc-cs-x" onClick={handleClose} aria-label="Close">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="6" y1="6" x2="18" y2="18" /><line x1="18" y1="6" x2="6" y2="18" /></svg>
          </button>
        </div>

        <div className="mc-cs-body">
          <div className="mc-cs-copying">
            <div><div className="k">Copying</div><div className="a">{traderName || shortenAddress(traderWallet)}</div></div>
            <span className="mc-cs-pill">Live manual</span>
          </div>

          <div className="mc-cs-note">
            This saves your <b>copy risk limits</b> for this trader. You still confirm each trade before a real Perpl order is placed.
          </div>

          <span className="mc-cs-lbl">Sizing mode</span>
          <div className="mc-seg">
            <button className="on">Fixed</button>
            <button disabled>Proportional (soon)</button>
          </div>

          <div className="mc-cs-grid">
            {field('Allocation', 'allocation_usd', 'USD', 'Paper capital')}
            {field('Max leverage', 'max_leverage', 'x', '1–20')}
            {field('Max margin', 'max_margin_per_trade', 'USD')}
            {field('Slippage', 'slippage_bps', 'bps', '0–500')}
            {field('Daily loss', 'max_daily_loss', 'USD')}
            {field('Total loss', 'max_total_loss', 'USD')}
          </div>

          <span className="mc-cs-lbl">Allowed markets</span>
          {marketsLoading ? (
            <div className="mc-err-line" style={{ color: 'var(--faint)' }}>Loading markets…</div>
          ) : marketsError ? (
            <div className="mc-err-line">Couldn't load markets — try again.</div>
          ) : activeMarkets.length === 0 ? (
            <div className="mc-err-line" style={{ color: 'var(--faint)' }}>No active markets available.</div>
          ) : (
            <div className="mc-mchips">
              {activeMarkets.map((m) => {
                const on = form.allowed_markets.includes(m.market_id);
                return (
                  <button key={m.market_id} className={clsx('mc-mchip', on && 'on')} onClick={() => toggleMarket(m.market_id)}>
                    <MarketLogo symbol={m.symbol} size={16} />{m.symbol}
                  </button>
                );
              })}
            </div>
          )}
          {errors.allowed_markets && <div className="mc-err-line">{errors.allowed_markets}</div>}

          <div className="mc-cs-toggle">
            <div className="tx">
              <div className="t1">Copy new positions only</div>
              <div className="t2">Ignore already-open positions</div>
            </div>
            <button className={clsx('mc-chk', form.copy_new_only && 'on')} onClick={() => set('copy_new_only', !form.copy_new_only)} role="switch" aria-checked={form.copy_new_only} aria-label="Copy new positions only">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><polyline points="20 6 9 17 4 12" /></svg>
            </button>
          </div>

          {!COPY_LIVE_ENABLED && (
            <div className="mc-cs-note" style={{ textAlign: 'center' }}>
              Live copy order placement is currently OFF — you can save settings now; no real orders are placed until it's enabled.
            </div>
          )}

          {apiError && <div className="mc-err-line">{apiError}</div>}
        </div>

        <div className="mc-cs-foot">
          <button className="mc-cs-save" onClick={handleSubmit} disabled={submitting || marketsLoading || !!marketsError}>
            {submitting ? 'Saving…' : marketsError ? 'Markets unavailable' : 'Save copy settings'}
          </button>
        </div>
      </div>
    </>,
    document.body,
  );
}

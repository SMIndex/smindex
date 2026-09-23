import { useState, useEffect } from 'react';
import { clsx } from 'clsx';
import Modal from '@/components/common/Modal';
import { COPY_LIVE_ENABLED } from '@/config/constants';
import { useMarkets } from '@/hooks/useMarkets';
import { shortenAddress } from '@/lib/formatters';
import type { CreateSubscriptionPayload, CopySubscription } from '@/lib/copyApi';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  traderWallet: string;
  traderName?: string | null;
  exchange?: string;   // signal source ('perpl' default | 'hl'); execution stays Perpl
  onSubmit: (payload: CreateSubscriptionPayload) => Promise<CopySubscription>;
}

interface FormState {
  max_basis_bps: string;   // HL signals only; '' = no basis gate
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
  max_basis_bps: '',
  allocation_usd: '100',
  max_leverage: '5',
  max_margin_per_trade: '50',
  max_daily_loss: '50',
  max_total_loss: '200',
  slippage_bps: '50',
  allowed_markets: [],          // populated from the live registry on open
  copy_new_only: true,
};

function num(v: string): number {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : NaN;
}

export default function StartPaperCopyModal({ isOpen, onClose, traderWallet, traderName, onSubmit, exchange }: Props) {
  const { activeMarkets, loading: marketsLoading, error: marketsError } = useMarkets();
  const [form, setForm] = useState<FormState>(DEFAULTS);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  // Default allowed_markets to ALL active markets once the registry loads (only
  // while the user hasn't picked anything). Never falls back to a hardcoded list.
  useEffect(() => {
    if (activeMarkets.length && form.allowed_markets.length === 0) {
      setForm((f) => (f.allowed_markets.length === 0
        ? { ...f, allowed_markets: activeMarkets.map((m) => m.market_id) }
        : f));
    }
  }, [activeMarkets]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const toggleMarket = (id: number) =>
    setForm((f) => ({
      ...f,
      allowed_markets: f.allowed_markets.includes(id)
        ? f.allowed_markets.filter((m) => m !== id)
        : [...f.allowed_markets, id],
    }));

  function validate(): Record<string, string> {
    const e: Record<string, string> = {};
    const alloc = num(form.allocation_usd);
    const lev = num(form.max_leverage);
    const margin = num(form.max_margin_per_trade);
    const daily = num(form.max_daily_loss);
    const total = num(form.max_total_loss);
    const slip = num(form.slippage_bps);

    if (!(alloc > 0)) e.allocation_usd = 'Allocation must be greater than 0';
    if (!(lev >= 1 && lev <= 20)) e.max_leverage = 'Leverage must be between 1 and 20';
    if (!(margin > 0)) e.max_margin_per_trade = 'Max margin per trade must be greater than 0';
    if (!(daily > 0)) e.max_daily_loss = 'Max daily loss must be greater than 0';
    if (!(total > 0)) e.max_total_loss = 'Max total loss must be greater than 0';
    if (!(slip >= 0 && slip <= 500)) e.slippage_bps = 'Slippage must be between 0 and 500 bps';
    if (form.allowed_markets.length === 0) e.allowed_markets = 'Select at least one market';
    return e;
  }

  const handleSubmit = async () => {
    setApiError(null);
    const e = validate();
    setErrors(e);
    if (Object.keys(e).length > 0) return;

    const payload: CreateSubscriptionPayload = {
      trader_wallet: traderWallet,
      exchange: exchange ?? 'perpl',
      max_basis_bps: exchange === 'hl' && form.max_basis_bps.trim() !== '' ? Math.round(num(form.max_basis_bps)) : null,
      sizing_mode: 'fixed', // fixed only for now
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
      setForm(DEFAULTS);
      setErrors({});
      onClose();
    } catch (err: any) {
      setApiError(err?.response?.data?.detail || err?.message || 'Failed to save copy settings');
    } finally {
      setSubmitting(false);
    }
  };

  const field = (
    label: string,
    key: keyof FormState,
    opts?: { suffix?: string; hint?: string; step?: string },
  ) => (
    <div>
      <label className="block text-xs font-medium text-text-secondary mb-1">{label}</label>
      <div className="relative">
        <input
          type="number"
          step={opts?.step ?? 'any'}
          value={form[key] as string}
          onChange={(ev) => set(key, ev.target.value as any)}
          className={clsx(
            'w-full bg-bg-secondary border rounded-lg px-3 py-2 text-sm text-text-primary outline-none focus:border-accent transition-colors tabular-nums',
            errors[key] ? 'border-danger/60' : 'border-text-secondary/15',
          )}
        />
        {opts?.suffix && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-text-secondary/50">{opts.suffix}</span>
        )}
      </div>
      {errors[key] ? (
        <p className="text-[11px] text-danger mt-1">{errors[key]}</p>
      ) : opts?.hint ? (
        <p className="text-[11px] text-text-secondary/50 mt-1">{opts.hint}</p>
      ) : null}
    </div>
  );

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Set up Copy" maxWidth="max-w-lg">
      <div className="space-y-4">
        {/* Trader + live notice */}
        <div className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg bg-bg-secondary/60 border border-text-secondary/10">
          <div>
            <div className="text-[10px] text-text-secondary/60 uppercase tracking-wide">Copying</div>
            <div className="text-sm font-semibold text-text-primary">
              {traderName || shortenAddress(traderWallet)}
            </div>
          </div>
          <span className="text-[10px] font-medium px-2 py-1 rounded-full bg-accent/10 text-accent border border-accent/20">
            Live manual
          </span>
        </div>

        <div className="px-3 py-2 rounded-lg bg-bg-secondary/60 border border-text-secondary/10 text-[11px] text-text-secondary">
          This saves your <span className="font-semibold text-text-primary">copy risk limits</span> for this trader. You still
          confirm each trade before a real Perpl order is placed.
        </div>

        {/* Sizing mode (fixed only) */}
        <div>
          <label className="block text-xs font-medium text-text-secondary mb-1">Sizing Mode</label>
          <div className="flex gap-2">
            <button className="flex-1 text-xs font-medium py-2 rounded-lg bg-accent/15 text-accent border border-accent/30 cursor-default">
              Fixed
            </button>
            <button disabled className="flex-1 text-xs font-medium py-2 rounded-lg bg-bg-secondary text-text-secondary/40 border border-text-secondary/10 cursor-not-allowed">
              Proportional (soon)
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          {field('Allocation', 'allocation_usd', { suffix: 'USD', hint: 'Total paper capital' })}
          {field('Max Leverage', 'max_leverage', { suffix: 'x', hint: '1–20' })}
          {field('Max Margin / Trade', 'max_margin_per_trade', { suffix: 'USD' })}
          {field('Slippage', 'slippage_bps', { suffix: 'bps', hint: '0–500' })}
          {field('Max Daily Loss', 'max_daily_loss', { suffix: 'USD' })}
          {field('Max Total Loss', 'max_total_loss', { suffix: 'USD' })}
          {exchange === 'hl' && field('Max Basis', 'max_basis_bps', { suffix: 'bps', hint: 'Block copy if |HL−Perpl| exceeds; empty = off' })}
        </div>

        {/* Markets — dynamic from the live Perpl registry (no hardcoded list) */}
        <div>
          <label className="block text-xs font-medium text-text-secondary mb-1.5">Allowed Markets</label>
          {marketsLoading ? (
            <div className="text-[11px] text-text-secondary/60">Loading markets…</div>
          ) : marketsError ? (
            <div className="text-[11px] text-danger">Couldn't load markets — try again.</div>
          ) : activeMarkets.length === 0 ? (
            <div className="text-[11px] text-text-secondary/60">No active markets available.</div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {activeMarkets.map((m) => {
                const active = form.allowed_markets.includes(m.market_id);
                return (
                  <button
                    key={m.market_id}
                    onClick={() => toggleMarket(m.market_id)}
                    className={clsx(
                      'text-xs font-medium px-3 py-1.5 rounded-lg border transition-colors',
                      active
                        ? 'bg-accent/15 text-accent border-accent/30'
                        : 'bg-bg-secondary text-text-secondary border-text-secondary/15 hover:text-text-primary',
                    )}
                  >
                    {m.symbol}
                  </button>
                );
              })}
            </div>
          )}
          {errors.allowed_markets && <p className="text-[11px] text-danger mt-1">{errors.allowed_markets}</p>}
        </div>

        {/* copy_new_only */}
        <label className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg bg-bg-secondary/60 border border-text-secondary/10 cursor-pointer">
          <div>
            <div className="text-sm font-medium text-text-primary">Copy new positions only</div>
            <div className="text-[11px] text-text-secondary/60">Ignore the trader's already-open positions</div>
          </div>
          <input
            type="checkbox"
            checked={form.copy_new_only}
            onChange={(e) => set('copy_new_only', e.target.checked)}
            className="w-4 h-4 accent-accent"
          />
        </label>

        {/* Live disabled notice */}
        {!COPY_LIVE_ENABLED && (
          <div className="text-[11px] text-text-secondary/50 text-center">
            Live copy order placement is currently OFF — you can save settings now; no real orders are placed until it's enabled.
          </div>
        )}

        {apiError && (
          <div className="px-3 py-2 rounded-lg bg-danger/10 border border-danger/25 text-xs text-danger">{apiError}</div>
        )}

        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={onClose}
            className="flex-1 text-sm font-medium py-2.5 rounded-lg bg-bg-secondary text-text-secondary hover:text-text-primary transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || marketsLoading || !!marketsError}
            className="flex-1 text-sm font-semibold py-2.5 rounded-lg bg-accent text-white hover:bg-accent/90 transition-colors disabled:opacity-50"
          >
            {submitting ? 'Saving…' : marketsError ? 'Markets unavailable' : 'Save Copy Settings'}
          </button>
        </div>
      </div>
    </Modal>
  );
}

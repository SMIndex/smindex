/**
 * The one SL/TP editor (2026-10-08). Used by the Terminal positions panel AND
 * the Copy dashboard, so both arm protection the same way.
 *
 * Every leg is a NATIVE Perpl trigger order (mt:22 with tp/tpc) placed through
 * the venue's own websocket. Nothing is stored on our side and nothing waits
 * for the price in this app, the old server-side watcher that "triggered"
 * rows in our database without ever reaching Perpl is gone.
 *
 * REPLACE, never add (the 2026-10-08 "two SL/TP pairs on one ETH position"
 * bug): arming a leg cancels the armed leg of the same kind for that position
 * first, so a position can never end up with two stop losses. An orphan leg
 * left behind after a close can act on a later position.
 */
import { useMemo, useRef, useState } from 'react';
import { clsx } from 'clsx';

import { MARKETS } from '@/config/constants';
import { triggerPrice, triggerSize, triggersFor, refreshBurst, type AccountState } from '@/lib/accountStore';
import { formatPrice } from '@/lib/formatters';
import { mobileConfirm } from '@/lib/mobileConfirm';
import { cancelOrder, placeSlTpOrder, requireMarketConfig } from '@/lib/perplTrading';
import { useToast } from '@/components/common/Toast';

export interface SlTpTarget {
  marketId: number;
  symbol: string;
  side: 'long' | 'short';
  size: number;
  markPrice: number;
  leverage?: number;
}

interface Props {
  account: AccountState;
  position: SlTpTarget;
  /** Called after any successful arm/cancel so the parent can refetch its own rows. */
  onChanged?: () => void;
  ensureConnected: () => Promise<boolean>;
  compact?: boolean;
}

export default function SlTpEditor({ account, position, onChanged, ensureConnected, compact }: Props) {
  const toast = useToast();
  const { sl, tp } = useMemo(
    () => triggersFor(account, position.marketId, position.side),
    [account, position.marketId, position.side],
  );
  const dec = MARKETS[position.marketId]?.decimals ?? 2;
  const [slInput, setSlInput] = useState('');
  const [tpInput, setTpInput] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const armedRow = (kind: 'sl' | 'tp', o: typeof sl) => {
    if (!o) return null;
    const px = triggerPrice(o);
    const sz = triggerSize(o);
    return (
      <div className="flex items-center gap-2 text-[10px]">
        <span className={clsx('font-semibold uppercase', kind === 'sl' ? 'text-danger' : 'text-success')}>
          {kind === 'sl' ? 'SL' : 'TP'}
        </span>
        <span className="text-text-primary">
          {px != null ? `$${formatPrice(px, dec)}` : 'price unavailable'}
        </span>
        <span className="text-text-secondary">
          {sz != null ? `· ${sz} ${position.symbol}` : '· size not reported by Perpl'}
        </span>
        {o.st === 9 && <span className="text-warning">· triggered</span>}
        <button
          onClick={() => cancelLeg(kind, o.oid)}
          disabled={busy === `cancel-${o.oid}`}
          className="ml-1 px-1.5 py-0.5 rounded bg-text-secondary/10 text-text-secondary hover:text-danger hover:bg-danger/10"
        >
          {busy === `cancel-${o.oid}` ? '…' : 'Cancel'}
        </button>
      </div>
    );
  };

  // One action at a time. `busy` used to be set only AFTER the reconnect wait
  // and the confirm, so a second click during a slow reconnect started the
  // whole flow again: on 2026-10-12 08:34 UTC one edit placed two stop losses
  // and two take profits on Perpl.
  const inFlight = useRef(false);

  const cancelLeg = async (kind: 'sl' | 'tp', oid: number) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(`cancel-${oid}`);
    try {
      if (!(await ensureConnected())) return;
      await cancelOrder({ marketId: position.marketId, oid });
      toast.success(`${kind.toUpperCase()} cancelled on Perpl`);
      refreshBurst('sltp-cancel');
      onChanged?.();
    } catch (e: any) {
      toast.error(`Cancel failed: ${e?.message || 'unknown error'}`);
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const arm = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      await armOnce();
    } finally {
      inFlight.current = false;
    }
  };

  const armOnce = async () => {
    const slPx = slInput ? parseFloat(slInput) : 0;
    const tpPx = tpInput ? parseFloat(tpInput) : 0;
    if (!slPx && !tpPx) { toast.error('Enter an SL and/or TP price'); return; }
    const mark = position.markPrice;
    if (slPx) {
      if (position.side === 'long' && slPx >= mark) { toast.error('SL must be below the current price for a long'); return; }
      if (position.side === 'short' && slPx <= mark) { toast.error('SL must be above the current price for a short'); return; }
    }
    if (tpPx) {
      if (position.side === 'long' && tpPx <= mark) { toast.error('TP must be above the current price for a long'); return; }
      if (position.side === 'short' && tpPx >= mark) { toast.error('TP must be below the current price for a short'); return; }
    }
    let mcfg;
    try {
      mcfg = requireMarketConfig(position.marketId);
    } catch {
      toast.error('Market settings not loaded. Refresh and try again.');
      return;
    }
    if (!(await ensureConnected())) return;

    const replacing = [slPx && sl ? 'SL' : '', tpPx && tp ? 'TP' : ''].filter(Boolean).join(' + ');
    const tx = await mobileConfirm({
      title: `Protect ${position.symbol} ${position.side}`,
      confirmLabel: 'Place on Perpl',
      rows: [
        ['Position', `${position.side} ${position.size} ${position.symbol}${position.leverage ? ` · ${position.leverage}x` : ''}`],
        ...(slPx ? [['Stop loss (mark)', formatPrice(slPx, dec)] as [string, string]] : []),
        ...(tpPx ? [['Take profit (mark)', formatPrice(tpPx, dec)] as [string, string]] : []),
        ['Size', `${position.size} ${position.symbol} (whole position)`],
        ...(replacing ? [['Replaces', `your armed ${replacing} on Perpl`] as [string, string]] : []),
        ['Fee when triggered', '$0.00 (close)'],
      ],
    });
    if (!tx.ok) return;

    setBusy('arm');
    let placed = 0;
    const errs: string[] = [];
    try {
      for (const [kind, px, existing] of [
        ['sl', slPx, sl] as const,
        ['tp', tpPx, tp] as const,
      ]) {
        if (!px) continue;
        // REPLACE: cancel the armed leg of this kind first. If the cancel
        // fails we do NOT arm a second one, two stop losses on one position
        // is worse than none being updated, and the user is told.
        if (existing) {
          try {
            await cancelOrder({ marketId: position.marketId, oid: existing.oid });
          } catch (e: any) {
            errs.push(`${kind.toUpperCase()}: could not cancel the armed leg (${e?.message || 'error'}), not replaced`);
            continue;
          }
        }
        try {
          await placeSlTpOrder({
            marketId: position.marketId, side: position.side, orderType: kind,
            triggerPrice: px, size: position.size,
            priceDecimals: mcfg.priceDecimals, sizeDecimals: mcfg.sizeDecimals,
          });
          placed++;
        } catch (e: any) {
          errs.push(`${kind.toUpperCase()} failed: ${e?.message || 'unknown error'}`);
        }
      }
      if (placed > 0) {
        tx.done(`${placed} trigger order${placed > 1 ? 's' : ''} armed on Perpl`);
        toast.success(`${placed} trigger order${placed > 1 ? 's' : ''} placed on Perpl for ${position.symbol}`);
        setSlInput(''); setTpInput('');
        refreshBurst('sltp-arm');
        window.dispatchEvent(new CustomEvent('order_placed'));
        onChanged?.();
      } else {
        tx.fail('No trigger order was placed');
      }
      errs.forEach((m) => toast.error(m));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={clsx('space-y-2', compact ? 'text-[10px]' : 'text-[11px]')}>
      {(sl || tp) ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="text-text-secondary uppercase tracking-wider text-[9px]">Armed on Perpl</span>
          {armedRow('sl', sl)}
          {armedRow('tp', tp)}
        </div>
      ) : (
        <div className="text-[10px] text-text-secondary">
          {account.venueSession
            ? 'No SL/TP armed on Perpl for this position.'
            : 'Connect to Perpl to see and place SL/TP (trigger orders live on Perpl, not on-chain).'}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={slInput}
          onChange={(e) => setSlInput(e.target.value)}
          inputMode="decimal"
          placeholder={sl ? `New SL (${triggerPrice(sl) ?? '-'})` : 'Stop loss'}
          className="w-28 px-2 py-1 rounded bg-bg-secondary border border-text-secondary/15 text-text-primary min-h-[32px] max-[820px]:min-h-[44px]"
        />
        <input
          value={tpInput}
          onChange={(e) => setTpInput(e.target.value)}
          inputMode="decimal"
          placeholder={tp ? `New TP (${triggerPrice(tp) ?? '-'})` : 'Take profit'}
          className="w-28 px-2 py-1 rounded bg-bg-secondary border border-text-secondary/15 text-text-primary min-h-[32px] max-[820px]:min-h-[44px]"
        />
        <button
          onClick={arm}
          disabled={busy === 'arm'}
          className="px-3 py-1 rounded bg-accent text-white font-semibold min-h-[32px] max-[820px]:min-h-[44px] disabled:opacity-50"
        >
          {busy === 'arm' ? 'Placing…' : (sl || tp) ? 'Replace on Perpl' : 'Place on Perpl'}
        </button>
      </div>
    </div>
  );
}

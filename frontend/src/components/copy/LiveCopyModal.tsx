import { useEffect, useMemo, useRef, useState } from 'react';
import { useAccount } from 'wagmi';
import { clsx } from 'clsx';
import Modal from '@/components/common/Modal';
import { useCopyTrade } from '@/hooks/useCopyTrade';
import { useAuth } from '@/hooks/useAuth';
import { useToast } from '@/components/common/Toast';
import { MARKET_CONFIGS, placeSlTpOrder } from '@/lib/perplTrading';
import { getOrderFeeRate, calculateEstimatedFee } from '@/lib/perplFees';
import {
  createCopyOrder, updateCopyOrder, copyOrderReasonLabel,
  getExecutionContext, getLiveCopyStatus, getSubscriptions,
  getTraderHlState, getTraderPositions, apiErrorMessage,
  type ExecutionContext, type LiveCopyStatus,
} from '@/lib/copyApi';
import { formatUSD, formatPrice, shortenAddress } from '@/lib/formatters';
import { relativeAge, utcDateTime } from '@/lib/time';
import { useCopyStore } from '@/stores/copyStore';
import { computeTrigger, type TpslMode } from '@/lib/tpsl';
import SmartMoneyContext from '@/components/analytics/SmartMoneyContext';

export interface LiveCopyPosition {
  market_id: number;
  symbol: string;
  side: 'long' | 'short';
  entry_price: number;
  mark_price: number;
  leverage: number;
  size: number;
  unrealized_pnl?: number | null;   // leader uPnL
  venue?: 'hl' | 'perpl';           // leader's venue (display; default hl)
  opened_at?: number | null;        // unix seconds UTC — leader's entry fill time
  opened_before?: number | null;    // honest lower bound when exact dating failed
  tp_px?: number | null;            // leader's own TP trigger (if any)
  sl_px?: number | null;            // leader's own SL trigger (if any)
  has_tpsl?: boolean;
}

interface Props {
  isOpen: boolean;
  onClose: () => void;
  traderWallet: string;
  traderName?: string | null;
  position: LiveCopyPosition;
  subscriptionId?: number | null;
  onCopied?: () => void;
}

const MIN_MARGIN_USD = 1;
const CTX_REFRESH_MS = 3000;
// Drift banner threshold: Perpl mark vs leader entry (Task 1 spec)
const DRIFT_WARN_BPS = 25;
// Real-money confirm: the button must be HELD this long before it fires
const HOLD_TO_CONFIRM_MS = 1000;
// Est.fill→est.liq distance below this % = "high liquidation risk" (red)
const LIQ_WARN_PCT = 5;
// Market-order slippage default (bps) — deliberate bound, not the venue cap
const DEFAULT_SLIPPAGE_BPS = 10;

const DASH = '—';

type Phase =
  | { kind: 'idle' }
  | { kind: 'authorizing' }
  | { kind: 'placing' }
  | { kind: 'filled'; price?: number; size?: number; orderId: string; triggerNote?: string }
  | { kind: 'resting'; orderId: string }
  | { kind: 'rejected'; reason: string };

// Live manual copy v2.1: every number server-fetched (3s refresh), venue-vs-venue
// context + drift so a minutes-old signal can be judged, hold-to-confirm, and
// order status driven by the trading websocket result (never a blind success).
export default function LiveCopyModal({ isOpen, onClose, traderWallet, traderName, position, subscriptionId, onCopied }: Props) {
  const { address } = useAccount();
  const { isAuthenticated, login, isLoading: authLoading } = useAuth();
  const { copyPosition, isCopying, status } = useCopyTrade();
  const toast = useToast();
  const mcfg = MARKET_CONFIGS[position.market_id];
  const configMissing = !mcfg || mcfg.priceDecimals == null || mcfg.sizeDecimals == null;
  const priceDec = mcfg?.priceDecimals ?? 2;
  const sizeDec = mcfg?.sizeDecimals ?? 4;
  const leaderVenue = position.venue ?? 'hl';

  // ---- server context (polled every 3s while open) ----
  const [ctx, setCtx] = useState<ExecutionContext | null>(null);
  const [ctxError, setCtxError] = useState(false);
  // audit A5: consecutive failed polls — >=3 greys the displayed values
  const [ctxFails, setCtxFails] = useState(0);
  // audit A6: fresh leader-state check at modal open
  const [leaderCheck, setLeaderCheck] = useState<
    { kind: 'pending' } | { kind: 'ok' } | { kind: 'gone' }
    | { kind: 'changed'; freshSize: number } | { kind: 'unverified' }>({ kind: 'pending' });
  const [liveStatus, setLiveStatus] = useState<LiveCopyStatus | null>(null);
  const [subMaxBasisBps, setSubMaxBasisBps] = useState<number | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });

  const pullStatus = () => getLiveCopyStatus().then(setLiveStatus).catch(() => setLiveStatus(null));
  useEffect(() => {
    if (!isOpen) return;
    let stop = false;
    const pull = () => getExecutionContext(position.market_id)
      .then((c) => { if (!stop) { setCtx(c); setCtxError(false); setCtxFails(0); } })
      .catch(() => { if (!stop) { setCtxError(true); setCtxFails((n) => n + 1); } });
    pull();
    const id = setInterval(pull, CTX_REFRESH_MS);
    pullStatus();
    if (subscriptionId) {
      getSubscriptions().then((subs) => {
        if (stop) return;
        const sub: any = subs.find((s: any) => s.id === subscriptionId);
        setSubMaxBasisBps(sub?.max_basis_bps ?? null);
      }).catch(() => {});
    }
    return () => { stop = true; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, position.market_id, subscriptionId]);

  // audit A6: ONE fresh leader-state read at open (<=300s server cache — the
  // point is not-older-than-cache, vs the static snapshot in the prop).
  // gone/flipped => refuse; size moved >25% => yellow note with fresh numbers;
  // fetch failure => proceed with an explicit "unverified" note (never silent).
  useEffect(() => {
    if (!isOpen) return;
    let stop = false;
    (async () => {
      try {
        let fresh: { side: string; size: number } | null = null;
        if (leaderVenue === 'hl') {
          const hs = await getTraderHlState(traderWallet);
          const p = (hs?.positions ?? []).find((x: any) => x.coin === position.symbol);
          if (p) fresh = { side: p.side, size: p.size };
        } else {
          const d = await getTraderPositions(traderWallet);
          const p = (d?.positions ?? []).find((x: any) => x.market_id === position.market_id || x.symbol === position.symbol);
          if (p) fresh = { side: p.side, size: p.size };
        }
        if (stop) return;
        if (!fresh || fresh.side !== position.side) setLeaderCheck({ kind: 'gone' });
        else if (position.size > 0 && Math.abs(fresh.size - position.size) / position.size > 0.25)
          setLeaderCheck({ kind: 'changed', freshSize: fresh.size });
        else setLeaderCheck({ kind: 'ok' });
      } catch {
        if (!stop) setLeaderCheck({ kind: 'unverified' });
      }
    })();
    return () => { stop = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, traderWallet, position.market_id]);

  // ---- order form state ----
  const maxLev = ctx?.max_leverage ?? mcfg?.maxLeverage ?? 20;
  // Default leverage mirrors the LEADER'S risk: min(leader, venue max). Once
  // the user moves the slider, their pick persists per venue (Zustand/localStorage)
  // and becomes the default on subsequent opens.
  const storedLev = useCopyStore((s) => s.lastLeverage[leaderVenue]);
  const setLastLeverage = useCopyStore((s) => s.setLastLeverage);
  const leaderLev = Math.max(1, Math.round(position.leverage || 1));
  const [leverage, setLeverage] = useState(() =>
    Math.min(Math.max(1, Math.round(storedLev || 0) || leaderLev), maxLev));
  useEffect(() => { setLeverage((l) => Math.min(l, maxLev)); }, [maxLev]);
  const [marginInput, setMarginInput] = useState('100');
  const [orderMode, setOrderMode] = useState<'market' | 'limit'>('market');
  const [limitInput, setLimitInput] = useState('');
  const limitPrefilled = useRef(false);
  useEffect(() => {
    if (!limitPrefilled.current && ctx?.mark_price) {
      setLimitInput(ctx.mark_price.toFixed(priceDec));
      limitPrefilled.current = true;
    }
  }, [ctx?.mark_price, priceDec]);
  // TP/SL mode: PRICE is the default (traders think in prices — the %-default
  // caused the live $41M-preview incident); the last-used mode is remembered
  // for the session and pre-selects both fields on the next open.
  const initialMode = ((): TpslMode => {
    const m = sessionStorage.getItem('perpl-tpsl-mode');
    return m === 'pct' ? 'pct' : 'price';
  })();
  const [tpMode, setTpModeRaw] = useState<TpslMode>(initialMode);
  const [slMode, setSlModeRaw] = useState<TpslMode>(initialMode);
  const setTpMode = (m: TpslMode) => { setTpModeRaw(m); sessionStorage.setItem('perpl-tpsl-mode', m); };
  const setSlMode = (m: TpslMode) => { setSlModeRaw(m); sessionStorage.setItem('perpl-tpsl-mode', m); };
  const [tpInput, setTpInput] = useState('');
  const [slInput, setSlInput] = useState('');
  const [slippageInput, setSlippageInput] = useState(String(DEFAULT_SLIPPAGE_BPS));
  // audit A7: ad-hoc basis guard — default 30 bps, editable, empty = cleared.
  // Subscription-scoped copies keep the subscription's own cap (field hidden).
  const [basisCapInput, setBasisCapInput] = useState('30');
  const [blocked, setBlocked] = useState<string | null>(null);

  const num = (s: string) => { const n = parseFloat(s); return Number.isFinite(n) && n > 0 ? n : 0; };
  const marginUsd = num(marginInput);
  const markPrice = ctx?.mark_price ?? null;
  const leaderMark = leaderVenue === 'hl' ? (ctx?.hl_mark ?? null) : markPrice;
  const limitPrice = num(limitInput);
  const refPrice = orderMode === 'limit' ? limitPrice : (markPrice ?? 0);
  const notional = marginUsd * leverage;
  // Size rounds DOWN to the market's size decimals (audit F1): the shared
  // placeOrder scaler uses Math.round, which rounded 0.0808625 ETH UP to
  // 0.081 ($150.26 notional on $150 margin-covered) and a $1 BTC edge UP by
  // 25.7%. Flooring here makes display == payload == never more than margin
  // covers; a floor to zero is honestly blocked by the size-zero gate.
  const size = refPrice > 0
    ? Math.floor((notional / refPrice) * 10 ** sizeDec) / 10 ** sizeDec
    : 0;
  const sideSign = position.side === 'long' ? 1 : -1;

  const slipCapBps = mcfg?.maxMarketSlippageBps ?? 100;
  const slippageBps = Math.min(num(slippageInput) || slipCapBps, slipCapBps);

  // Drift: current Perpl mark vs the LEADER'S entry — the "is this entry still
  // worth copying" number. Negative-for-long / positive-for-short = favorable.
  const driftBps = markPrice != null && position.entry_price > 0
    ? ((markPrice - position.entry_price) / position.entry_price) * 10000 : null;
  const driftFavorable = driftBps != null && driftBps * sideSign < 0;
  const driftWarn = driftBps != null && Math.abs(driftBps) > DRIFT_WARN_BPS;

  // Cross-venue basis right now (both marks live from the existing feeds)
  const liveBasisBps = ctx?.basis_bps ?? null;

  // Validated trigger computation (lib/tpsl): a result carries EITHER a valid
  // price OR an inline error/hint — an absurd preview can never render.
  const tpRes = useMemo(() => computeTrigger('tp', tpMode, tpInput, refPrice, position.side),
    [tpMode, tpInput, refPrice, position.side]);
  const slRes = useMemo(() => computeTrigger('sl', slMode, slInput, refPrice, position.side),
    [slMode, slInput, refPrice, position.side]);
  const tpPrice = tpRes.price;
  const slPrice = slRes.price;
  const tpPnl = tpPrice != null && size > 0 ? (tpPrice - refPrice) * size * sideSign : null;
  const slPnl = slPrice != null && size > 0 ? (slPrice - refPrice) * size * sideSign : null;
  const tpUnresolved = tpInput.trim() !== '' && tpPrice == null;
  const slUnresolved = slInput.trim() !== '' && slPrice == null;

  const liqPrice = useMemo(() => {
    if (!mcfg?.maintenanceMarginHdths || size <= 0 || refPrice <= 0) return null;
    const mmr = notional * (100 / mcfg.maintenanceMarginHdths);
    return position.side === 'long'
      ? refPrice - (marginUsd - mmr) / size
      : refPrice + (marginUsd - mmr) / size;
  }, [mcfg, size, refPrice, notional, marginUsd, position.side]);

  // Est. fill for a market order = mark worst-cased by the slippage bound
  const estFillPrice = orderMode === 'limit'
    ? (limitPrice > 0 ? limitPrice : null)
    : (markPrice != null ? markPrice * (1 + sideSign * slippageBps / 10000) : null);

  const feeRate = mcfg ? getOrderFeeRate(mcfg, { isLimit: orderMode === 'limit', postOnly: false }) : null;
  const estFee = feeRate ? calculateEstimatedFee(notional, feeRate.bps) : null;

  // Distance from est. fill to est. liq — the practical liquidation cushion
  const liqDistPct = liqPrice != null && estFillPrice != null && estFillPrice > 0
    ? Math.abs(liqPrice - estFillPrice) / estFillPrice * 100 : null;

  // Effective basis cap: subscription's own cap when subscribed, else the
  // ad-hoc field (empty = cleared = no cap). Server enforces the same value.
  const adhocCap = basisCapInput.trim() === '' ? 0 : Math.max(0, Math.round(num(basisCapInput)));
  const effectiveBasisCap = subscriptionId != null ? subMaxBasisBps : (adhocCap > 0 ? adhocCap : null);
  const basisExceeds = liveBasisBps != null && effectiveBasisCap != null && Math.abs(liveBasisBps) > effectiveBasisCap;
  const balance = ctx?.available_balance ?? null;
  const insufficient = balance != null && marginUsd > balance;
  const belowMin = marginUsd < MIN_MARGIN_USD;
  const liveAvailable = !!liveStatus?.live_available;
  const feedsReady = markPrice != null && !ctxError && !!ctx?.market_active;

  // ---- SANE gating: one explicit reason at a time, always shown under the button ----
  const disableReason: string | null = (() => {
    if (phase.kind === 'placing' || phase.kind === 'authorizing') return null; // busy, not "disabled"
    if (!isAuthenticated) return 'Sign in with your wallet to enable live copy.';
    if (liveStatus && !liveStatus.live_enabled) return 'Live copy is disabled by the kill switch (COPY_LIVE_ENABLED).';
    if (liveStatus && liveStatus.live_enabled && !liveStatus.allowlisted) return 'Your wallet is not in the live-copy allowlist (LIVE_COPY_ALLOWLIST).';
    if (configMissing) return 'Market config unavailable — cannot size the order safely.';
    if (ctxError) return 'Execution context failed to load — retrying every 3s.';
    if (markPrice == null) return 'Waiting for live Perpl mark price…';
    if (ctx && !ctx.market_active) return 'Market is not active on Perpl.';
    if (balance == null) return 'No Perpl balance readable for your wallet — a funded Perpl account is required.';
    if (insufficient) return `Margin exceeds your available balance (${formatUSD(balance)}).`;
    if (belowMin) return `Margin must be at least ${formatUSD(MIN_MARGIN_USD)}.`;
    if (orderMode === 'limit' && limitPrice <= 0) return 'Enter a limit price.';
    if (size <= 0) return 'Size computes to zero — increase margin or leverage.';
    // audit A8: venue min posting size, pre-checked before signing (live value
    // is currently 0 on all markets — dormant until the venue sets one)
    if (ctx?.min_size != null && size < ctx.min_size) return `Below the venue minimum size (${ctx.min_size} ${position.symbol}).`;
    if (leaderCheck.kind === 'gone') return 'Leader has closed or changed this position.';
    if (tpUnresolved) return 'Fix the Take-profit input (see the note under the field).';
    if (slUnresolved) return 'Fix the Stop-loss input (see the note under the field).';
    return null;
  })();
  const canPlace = disableReason === null && phase.kind !== 'placing' && phase.kind !== 'authorizing' && !isCopying;

  // ---- hold-to-confirm ----
  const [holdPct, setHoldPct] = useState(0);
  const holdTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const holdStart = useRef(0);
  const stopHold = () => {
    if (holdTimer.current) clearInterval(holdTimer.current);
    holdTimer.current = null;
    setHoldPct(0);
  };
  const startHold = () => {
    if (!canPlace || holdTimer.current) return;
    holdStart.current = Date.now();
    holdTimer.current = setInterval(() => {
      const pct = Math.min(100, ((Date.now() - holdStart.current) / HOLD_TO_CONFIRM_MS) * 100);
      setHoldPct(pct);
      if (pct >= 100) {
        stopHold();
        void handleCopy();
      }
    }, 40);
  };
  useEffect(() => stopHold, []);

  const handleCopy = async () => {
    setBlocked(null);
    if (!address) { toast.error('Connect your wallet first'); return; }
    setPhase({ kind: 'authorizing' });

    // 1) Record the confirmed attempt + run the backend gate ladder.
    // Audit A12 (ACCEPTED BY DESIGN): the key is per-confirmation, so two open
    // tabs can each authorize one order — idempotency protects RETRIES of the
    // same confirmed attempt (same key, at-most-once rq on the venue), not
    // duplicate human intents. Hold-to-confirm + per-modal phase guard cover
    // the accidental-double-click case.
    const idempotency_key = `manual:${address.toLowerCase()}:${position.market_id}:${position.side}:${Date.now()}`;
    let order;
    try {
      order = await createCopyOrder({
        trader_wallet: traderWallet,
        market_id: position.market_id,
        symbol: position.symbol,
        side: position.side,
        intended_size: size,
        intended_price: refPrice,
        leverage,
        allocation_usd: marginUsd,
        subscription_id: subscriptionId ?? null,
        idempotency_key,
        order_type: orderMode,
        tp_price: tpPrice,
        sl_price: slPrice,
        // A7: ad-hoc cap (subscription copies keep the sub's own cap server-side)
        basis_cap_bps: subscriptionId != null ? undefined : adhocCap,
      });
    } catch (e: any) {
      setPhase({ kind: 'rejected', reason: apiErrorMessage(e) || 'Could not start copy' });
      return;
    }

    // 2) Only place the REAL order if the backend authorized it.
    if (order.status !== 'submitted') {
      const reason = copyOrderReasonLabel(order.skip_reason, order.status);
      setBlocked(reason);
      setPhase({ kind: 'rejected', reason });
      return;
    }

    // 2b) Kill-switch revalidation at the last possible moment (audit F4):
    // orders are wallet-signed CLIENT-side, so the server cannot stop one
    // after the go-ahead — recheck live-status right before signing to shrink
    // the flip window from "until placement" to ~one round-trip. Fail-open on
    // network error is deliberate: the authoritative gate already passed.
    try {
      const ls = await getLiveCopyStatus();
      if (ls && !ls.live_available) {
        await updateCopyOrder(order.id, {
          status: 'failed',
          error_message: 'aborted: live copy was disabled between authorization and placement',
        }).catch(() => {});
        setPhase({ kind: 'rejected', reason: 'Live copy was disabled just now — order not placed.' });
        return;
      }
    } catch { /* status unreachable: proceed on the standing authorization */ }

    // 3) Place the real Perpl order (wallet-signed, result comes from the
    //    trading websocket mt:24 order update — not an assumed success).
    setPhase({ kind: 'placing' });
    try {
      const result: any = await copyPosition({
        marketId: position.market_id,
        symbol: position.symbol,
        side: position.side,
        size,
        leverage,
        entryPrice: markPrice ?? refPrice,
        orderMode,
        limitPrice: orderMode === 'limit' ? limitPrice : undefined,
        slippageBps: orderMode === 'market' ? slippageBps : undefined,
        source: 'copy_trade',
      });

      const perplOrderId = result?.orderId != null ? String(result.orderId) : null;
      const confirmedFill = result?.filled === true && Number(result?.filledSize) > 0;
      const restingInBook = result?.placed === true && !confirmedFill && !!perplOrderId;

      if (confirmedFill && perplOrderId) {
        const fillPrice = result?.filledPrice ? result.filledPrice / (10 ** priceDec) : undefined;
        const fillSize = result?.filledSize ? result.filledSize / (10 ** sizeDec) : undefined;

        // Native venue TP/SL triggers for the FILLED size — audit A1: one
        // bounded retry after 2s per trigger; a final failure is NEVER silent
        // (in-modal note + toast + triggers_failed marker -> position badge +
        // telegram via the PATCH below).
        const placeTriggerWithRetry = async (orderType: 'tp' | 'sl', triggerPrice: number): Promise<string | null> => {
          for (let attempt = 0; attempt < 2; attempt++) {
            try {
              const r = await placeSlTpOrder({
                marketId: position.market_id, side: position.side, orderType,
                triggerPrice, size: trigSize, priceDecimals: priceDec, sizeDecimals: sizeDec,
              });
              return r?.orderId != null ? String(r.orderId) : null;
            } catch (e: any) {
              if (attempt === 0) { await new Promise((res) => setTimeout(res, 2000)); continue; }
              triggerErrs.push(`${orderType.toUpperCase()} failed after retry: ${e?.message || 'error'}`);
            }
          }
          return null;
        };
        let tpOrderId: string | null = null;
        let slOrderId: string | null = null;
        const triggerErrs: string[] = [];
        const trigSize = fillSize ?? size;
        if (tpPrice != null) tpOrderId = await placeTriggerWithRetry('tp', tpPrice);
        if (slPrice != null) slOrderId = await placeTriggerWithRetry('sl', slPrice);

        await updateCopyOrder(order.id, {
          status: 'filled',
          perpl_order_id: perplOrderId,
          fill_price: fillPrice ?? null,
          fill_size: fillSize ?? null,
          tp_order_id: tpOrderId,
          sl_order_id: slOrderId,
          error_message: triggerErrs.length ? triggerErrs.join('; ') : null,
          // A1: marker propagates to the position row badge + telegram alert
          triggers_failed: triggerErrs.length > 0,
        }).catch(() => {});
        setPhase({
          kind: 'filled', price: fillPrice, size: fillSize, orderId: perplOrderId,
          triggerNote: triggerErrs.length ? triggerErrs.join('; ')
            : (tpOrderId || slOrderId) ? `TP/SL armed (${[tpOrderId && 'TP', slOrderId && 'SL'].filter(Boolean).join(' + ')})` : undefined,
        });
        onCopied?.();
      } else if (restingInBook) {
        await updateCopyOrder(order.id, { status: 'placed', perpl_order_id: perplOrderId }).catch(() => {});
        setPhase({ kind: 'resting', orderId: perplOrderId! });
        onCopied?.();
      } else {
        await updateCopyOrder(order.id, {
          status: 'failed',
          error_message: 'Not confirmed on Perpl (no fill/order id) — not marked as live',
        }).catch(() => {});
        setPhase({ kind: 'rejected', reason: 'Order was not confirmed on Perpl — nothing was marked live.' });
      }
    } catch (e: any) {
      await updateCopyOrder(order.id, {
        status: 'failed',
        error_message: e?.message || 'Order failed',
      }).catch(() => {});
      setPhase({ kind: 'rejected', reason: e?.message || 'Order failed' });
    }
  };

  const fmt = (v: number | null | undefined, dec = 2) => (v == null || !Number.isFinite(v) ? DASH : formatPrice(v, dec));
  const leaderUpnl = position.unrealized_pnl;
  const venueLabel = leaderVenue === 'hl' ? 'Hyperliquid' : 'Perpl';
  const ageStr = position.opened_at ? `filled ${relativeAge(position.opened_at)} ago`
    : position.opened_before ? `opened >${relativeAge(position.opened_before)} ago` : null;

  const staleCtx = ctxFails >= 3;   // audit A5

  if (leaderCheck.kind === 'gone') {
    // audit A6: the leader no longer holds this position (or flipped side) —
    // refuse instead of letting the user copy into an exited trade.
    return (
      <Modal isOpen={isOpen} onClose={onClose} title="Copy Live Trade" maxWidth="max-w-lg"
        closeOnOverlayClick={false} closeOnEscape={false}>
        <div className="space-y-4 py-4 text-center">
          <div className="text-sm font-semibold text-danger">Leader has closed or changed this position</div>
          <p className="text-xs text-text-secondary">
            A fresh read of {shortenAddress(traderWallet)}'s {venueLabel} state no longer shows an open
            {' '}{position.side.toUpperCase()} {position.symbol} position matching this card.
            Reopen their profile for current positions.
          </p>
          <button type="button" onClick={onClose} className="text-sm font-medium px-6 py-2.5 rounded-lg bg-bg-secondary text-text-primary">Close</button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Copy Live Trade" maxWidth="max-w-lg"
      closeOnOverlayClick={false} closeOnEscape={false}>
      <div className="space-y-3">
        {/* Trader + LIVE marker */}
        <div className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg bg-bg-secondary/60 border border-text-secondary/10">
          <div>
            <div className="text-[10px] text-text-secondary/60 uppercase tracking-wide">Copying</div>
            <div className="text-sm font-semibold text-text-primary">{traderName || shortenAddress(traderWallet)}</div>
          </div>
          <span className="text-[10px] font-medium px-2 py-1 rounded-full bg-danger/10 text-danger border border-danger/20">LIVE</span>
        </div>

        {/* Leader position */}
        <div className="p-3 bg-bg-secondary rounded-lg">
          <div className="flex items-center justify-between mb-1.5">
            <div className="text-[10px] text-text-secondary/60 uppercase tracking-wide">Leader position · {venueLabel}</div>
            {ageStr && (
              <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-bg-primary text-text-secondary"
                title={position.opened_at ? utcDateTime(position.opened_at) : undefined}>{ageStr}</span>
            )}
          </div>
          <div className="grid grid-cols-5 gap-2 text-xs">
            <div><span className="text-text-secondary/60 block">Side</span>
              <span className={clsx('font-bold uppercase', position.side === 'long' ? 'text-success' : 'text-danger')}>{position.side}</span></div>
            <div><span className="text-text-secondary/60 block">Entry</span>
              <span className="font-bold text-text-primary tabular-nums">${fmt(position.entry_price, priceDec)}</span></div>
            <div><span className="text-text-secondary/60 block">Lev</span>
              <span className="font-bold text-text-primary tabular-nums">{position.leverage ? `${position.leverage}x` : DASH}</span></div>
            <div><span className="text-text-secondary/60 block">Size</span>
              <span className="font-bold text-text-primary tabular-nums" title={`${position.size} ${position.symbol}`}>
                {position.size ? position.size.toLocaleString('en-US', { maximumFractionDigits: 4 }) : DASH}
                <span className="block text-[10px] font-normal text-text-secondary/70">{position.size && position.mark_price ? formatUSD(position.size * position.mark_price) : ''}</span></span></div>
            <div><span className="text-text-secondary/60 block">uPnL</span>
              <span className={clsx('font-bold tabular-nums', leaderUpnl == null ? 'text-text-primary' : leaderUpnl >= 0 ? 'text-success' : 'text-danger')}>
                {leaderUpnl == null ? DASH : formatUSD(leaderUpnl)}</span></div>
          </div>
          {position.has_tpsl && (position.tp_px != null || position.sl_px != null) && (
            <div className="flex gap-2 mt-2 text-[10.5px]">
              {position.tp_px != null && <span className="px-1.5 py-0.5 rounded bg-success/10 text-success tabular-nums">Leader TP ${fmt(position.tp_px, priceDec)}</span>}
              {position.sl_px != null && <span className="px-1.5 py-0.5 rounded bg-danger/10 text-danger tabular-nums">Leader SL ${fmt(position.sl_px, priceDec)}</span>}
            </div>
          )}
        </div>

        {/* audit A6: freshness notes (gone-case replaces the whole body above) */}
        {leaderCheck.kind === 'changed' && (
          <div className="p-2.5 bg-warning/5 border border-warning/25 rounded-lg text-[11px] text-text-secondary">
            <span className="text-warning font-semibold">Leader's position size changed:</span>{' '}
            now {leaderCheck.freshSize.toLocaleString('en-US', { maximumFractionDigits: 4 })} {position.symbol} (card showed {position.size.toLocaleString('en-US', { maximumFractionDigits: 4 })}).
          </div>
        )}
        {leaderCheck.kind === 'unverified' && (
          <div className="p-2.5 bg-warning/5 border border-warning/25 rounded-lg text-[11px] text-text-secondary">
            <span className="text-warning font-semibold">Leader state unverified</span> — the fresh check failed; the numbers above are the snapshot from when you opened the profile.
          </div>
        )}

        {/* Market now — both venues, side by side (A5: greys when polls fail) */}
        <div className={clsx('p-3 bg-bg-secondary rounded-lg transition-opacity', staleCtx && 'opacity-50')}>
          <div className="flex items-center justify-between mb-1.5">
            <div className="text-[10px] text-text-secondary/60 uppercase tracking-wide">Market now · {position.symbol}</div>
            {ctxError && <span className="text-[10px] text-danger">{staleCtx ? 'stale — feed down, values are last known' : 'context unavailable — retrying'}</span>}
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div><span className="text-text-secondary/60 block">{venueLabel} mark</span>
              <span className="font-bold text-text-primary tabular-nums">{leaderMark == null ? DASH : `$${fmt(leaderMark, priceDec)}`}</span></div>
            <div><span className="text-text-secondary/60 block">Perpl mark (you)</span>
              <span className="font-bold text-text-primary tabular-nums">{markPrice == null ? DASH : `$${fmt(markPrice, priceDec)}`}</span></div>
            <div><span className="text-text-secondary/60 block">Basis</span>
              <span className={clsx('font-bold tabular-nums', liveBasisBps == null ? 'text-text-primary' : Math.abs(liveBasisBps) > 25 ? 'text-danger' : Math.abs(liveBasisBps) > 10 ? 'text-warning' : 'text-success')}>
                {liveBasisBps == null ? DASH : `${liveBasisBps > 0 ? '+' : ''}${liveBasisBps.toFixed(1)} bps`}</span></div>
          </div>
          {/* Drift vs leader entry — the chase indicator */}
          {driftBps != null && (
            <div className={clsx('mt-2 text-[11px] tabular-nums font-medium', driftFavorable ? 'text-success' : 'text-danger')}>
              Perpl mark is {Math.abs(driftBps).toFixed(1)} bps ({(Math.abs(driftBps) / 100).toFixed(2)}%) {driftBps >= 0 ? 'above' : 'below'} the leader's entry
              {' '}· {driftFavorable ? 'favorable for copying this side' : 'you would be chasing'}
            </div>
          )}
        </div>

        {driftWarn && (
          <div className="p-3 bg-warning/5 border border-warning/25 rounded-lg text-xs text-text-secondary">
            <span className="text-warning font-semibold">Price has moved {Math.abs(driftBps!).toFixed(0)} bps since the leader's entry.</span>{' '}
            The signal is {ageStr ? ageStr.replace('filled ', '') + ' old' : 'not fresh'} — judge whether this entry is still worth copying.
          </div>
        )}

        {/* Tier-2 B2: smart-money context — display-only, NEVER gates the order */}
        <SmartMoneyContext asset={position.symbol} side={position.side} />

        {/* Order type */}
        <div className="grid grid-cols-2 gap-1 p-1 rounded-lg bg-bg-secondary">
          {(['market', 'limit'] as const).map((m) => (
            <button key={m} type="button" onClick={() => setOrderMode(m)}
              className={clsx('text-xs font-semibold py-1.5 rounded-md transition-colors capitalize',
                orderMode === m ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary')}>{m}</button>
          ))}
        </div>

        {/* Margin / leverage / limit price / slippage */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1">Margin (USD)</label>
            <input type="number" inputMode="decimal" value={marginInput}
              onChange={(e) => setMarginInput(e.target.value)} min={1} step={1}
              className="w-full bg-bg-secondary border border-text-secondary/15 rounded-lg px-3 py-2 text-sm text-text-primary outline-none focus:border-accent tabular-nums" />
          </div>
          {orderMode === 'limit' ? (
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">Limit price (USD)</label>
              <input type="number" inputMode="decimal" value={limitInput}
                onChange={(e) => setLimitInput(e.target.value)} min={0}
                className="w-full bg-bg-secondary border border-text-secondary/15 rounded-lg px-3 py-2 text-sm text-text-primary outline-none focus:border-accent tabular-nums" />
              {/* audit A10: crossing limit executes immediately as taker */}
              {markPrice != null && limitPrice > 0 && (sideSign > 0 ? limitPrice >= markPrice : limitPrice <= markPrice) && (
                <div className="text-[10px] text-warning mt-1">Crosses the book — will fill immediately as taker (6.9 bps).</div>
              )}
            </div>
          ) : (
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">Max slippage (bps, cap {slipCapBps})</label>
              <input type="number" inputMode="numeric" value={slippageInput} placeholder={String(slipCapBps)}
                onChange={(e) => setSlippageInput(e.target.value)} min={1} max={slipCapBps}
                className="w-full bg-bg-secondary border border-text-secondary/15 rounded-lg px-3 py-2 text-sm text-text-primary outline-none focus:border-accent tabular-nums" />
              <div className="text-[10px] text-text-secondary/70 mt-1">
                Max price movement allowed for your fill — order rejects if exceeded.
              </div>
            </div>
          )}
        </div>
        <div>
          <div className="flex justify-between text-sm mb-1.5">
            <label className="font-medium text-text-primary">Leverage</label>
            <span className="text-accent font-bold">{leverage}x</span>
          </div>
          <input type="range" value={leverage}
            onChange={(e) => {
              const v = Number(e.target.value);
              setLeverage(v);
              setLastLeverage(leaderVenue, v);   // user's pick persists per venue
            }}
            min={1} max={maxLev} step={1} className="w-full accent-accent" />
          <div className="flex justify-between text-[10px] text-text-secondary mt-0.5"><span>1x</span><span>{maxLev}x max ({position.symbol} on Perpl)</span></div>
          <div className="flex justify-between text-[10px] mt-0.5">
            <span className={clsx(position.leverage && leverage !== Math.min(leaderLev, maxLev) ? 'text-warning' : 'text-text-secondary/70')}>
              {position.leverage ? `Leader uses ${position.leverage}x` : ''}
            </span>
            {liqDistPct != null && (
              <span className={clsx('tabular-nums', liqDistPct < LIQ_WARN_PCT ? 'text-danger font-semibold' : 'text-text-secondary/70')}>
                liq {liqDistPct.toFixed(1)}% from est. fill{liqDistPct < LIQ_WARN_PCT ? ' — high liquidation risk at this leverage' : ''}
              </span>
            )}
          </div>
        </div>

        {/* TP / SL — price mode default; segmented mode toggle + input
            adornment; validated by lib/tpsl (absurd previews cannot render) */}
        <div className="grid grid-cols-2 gap-3">
          {([['tp', 'Take profit', tpMode, setTpMode, tpInput, setTpInput, tpRes, tpPnl],
             ['sl', 'Stop loss', slMode, setSlMode, slInput, setSlInput, slRes, slPnl]] as const).map(
            ([key, label, mode, setMode, input, setInput, res, pnl]) => (
            <div key={key} className="p-2.5 bg-bg-secondary rounded-lg">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[11px] font-medium text-text-secondary">{label}{' '}
                  <span className="text-text-secondary/50">{mode === 'pct' ? '(% of entry)' : '(optional)'}</span></span>
                {/* segmented mode control — active segment SOLID */}
                <div className="flex gap-0 p-0.5 rounded-md bg-bg-primary border border-text-secondary/15">
                  {(['price', 'pct'] as const).map((m) => (
                    <button key={m} type="button" onClick={() => (setMode as any)(m)}
                      title={m === 'pct' ? '% move from the entry price (your limit price, or the current Perpl mark for market orders)' : 'absolute trigger price in USD'}
                      className={clsx('text-[9px] px-2 py-0.5 rounded font-semibold transition-colors',
                        mode === m ? 'bg-accent text-white' : 'text-text-secondary/60 hover:text-text-primary')}>
                      {m === 'pct' ? '%' : '$'}</button>
                  ))}
                </div>
              </div>
              <div className="relative">
                {mode === 'price' && <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[11px] text-text-secondary/60 pointer-events-none">$</span>}
                <input type="number" inputMode="decimal" value={input}
                  placeholder={mode === 'pct' ? 'e.g. 3' : 'trigger price'}
                  onChange={(e) => (setInput as any)(e.target.value)}
                  className={clsx('w-full bg-bg-primary border rounded-md py-1.5 text-xs text-text-primary outline-none focus:border-accent tabular-nums',
                    mode === 'price' ? 'pl-5 pr-2' : 'pl-2 pr-6',
                    res.error ? 'border-danger/50' : 'border-text-secondary/15')} />
                {mode === 'pct' && <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[11px] text-text-secondary/60 pointer-events-none">%</span>}
              </div>
              {res.error && <div className="text-[10px] mt-1 text-danger">{res.error}</div>}
              {res.looksLikePrice && (
                <div className="text-[10px] mt-1 text-warning">
                  This looks like a price —{' '}
                  <button type="button" className="underline font-semibold"
                    onClick={() => (setMode as any)('price')}>switch to $ mode?</button>
                </div>
              )}
              {res.price != null && (
                <div className="text-[10px] mt-1 tabular-nums text-text-secondary">
                  trigger ${fmt(res.price, priceDec)} · est {pnl != null && pnl >= 0 ? '+' : ''}{pnl == null ? DASH : formatUSD(pnl)}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* audit A7: ad-hoc basis guard — server-enforced, editable, clearable */}
        {subscriptionId == null && (
          <div className="flex items-center justify-between p-2.5 bg-bg-secondary rounded-lg">
            <label className="text-[11px] font-medium text-text-secondary"
              title="Blocks the order server-side when |Perpl vs Hyperliquid| basis exceeds this cap at placement time (fail-closed if basis is unmeasurable). Clear the field to disable the guard.">
              Basis guard (bps){adhocCap === 30 && basisCapInput === '30' ? <span className="text-text-secondary/50"> · 30 bps (default)</span> : adhocCap === 0 ? <span className="text-warning"> · cleared</span> : null}
            </label>
            <input type="number" inputMode="numeric" value={basisCapInput} min={0} max={1000}
              onChange={(e) => setBasisCapInput(e.target.value)}
              className="w-20 bg-bg-primary border border-text-secondary/15 rounded-md px-2 py-1.5 text-xs text-text-primary outline-none focus:border-accent tabular-nums text-right" />
          </div>
        )}

        {/* Your execution preview (A5: greys when polls fail) */}
        <div className={clsx('p-3 bg-bg-secondary rounded-lg transition-opacity', staleCtx && 'opacity-50')}>
          <div className="text-[10px] text-text-secondary/60 uppercase tracking-wide mb-1.5">Your execution preview · Perpl{staleCtx ? ' · stale' : ''}</div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
            <div className="flex justify-between"><span className="text-text-secondary">Est. fill ({orderMode})</span><span className="font-bold text-text-primary tabular-nums">{estFillPrice == null ? DASH : `$${fmt(estFillPrice, priceDec)}`}</span></div>
            <div className="flex justify-between"><span className="text-text-secondary">Notional</span><span className="font-bold text-text-primary tabular-nums">{formatUSD(notional)}</span></div>
            <div className="flex justify-between"><span className="text-text-secondary">Size</span><span className="font-bold text-text-primary tabular-nums">{size > 0 ? `${size.toFixed(sizeDec)} ${position.symbol}` : DASH}</span></div>
            <div className="flex justify-between"><span className="text-text-secondary">Est. fee ({feeRate?.kind ?? 'taker'})</span><span className="font-bold text-text-primary tabular-nums">{estFee == null ? DASH : formatUSD(estFee)}</span></div>
            <div className="flex justify-between"><span className="text-text-secondary">Est. liq price</span>
              <span className={clsx('font-bold tabular-nums', liqDistPct != null && liqDistPct < LIQ_WARN_PCT ? 'text-danger' : 'text-warning')}>
                {liqPrice == null ? DASH : `$${fmt(liqPrice, priceDec)}`}
                {liqDistPct != null && <span className="font-normal text-[10px]"> ({liqDistPct.toFixed(1)}% away)</span>}
              </span></div>
            <div className="flex justify-between"><span className="text-text-secondary">Funding /interval</span>
              <span className={clsx('font-bold tabular-nums', ctx?.funding_rate == null ? 'text-text-primary' : (ctx.funding_rate * sideSign > 0 ? 'text-danger' : 'text-success'))}>
                {ctx?.funding_rate == null ? DASH : `${(ctx.funding_rate * 100).toFixed(4)}% ${ctx.funding_rate * sideSign > 0 ? 'you pay' : 'you receive'}`}</span></div>
            <div className="flex justify-between"><span className="text-text-secondary">Available balance</span>
              <span className={clsx('font-bold tabular-nums', insufficient ? 'text-danger' : 'text-text-primary')}>{balance == null ? DASH : formatUSD(balance)}</span></div>
          </div>
        </div>

        {/* Honest warnings */}
        <div className="p-3 bg-danger/5 border border-danger/20 rounded-lg">
          <p className="text-xs text-text-secondary">
            <span className="text-danger font-semibold">This places a real Perpl order</span> with your own funds at {leverage}x leverage.
            Copy trading does not guarantee profit and can lose money.
          </p>
        </div>
        {basisExceeds && (
          <div className="p-3 bg-warning/5 border border-warning/25 rounded-lg text-xs text-text-secondary">
            <span className="text-warning font-semibold">Basis {liveBasisBps!.toFixed(1)} bps exceeds the {effectiveBasisCap} bps cap.</span>{' '}
            The server gate will block this order — {subscriptionId != null ? 'adjust your subscription limit' : 'raise or clear the basis guard'} or wait for the gap to close.
          </div>
        )}

        {/* Order result — from the trading websocket, shown in place */}
        {phase.kind === 'filled' && (
          <div className="p-3 bg-success/10 border border-success/30 rounded-lg text-xs">
            <div className="font-semibold text-success">Filled on Perpl</div>
            <div className="text-text-secondary tabular-nums mt-0.5">
              {phase.size != null ? `${phase.size.toFixed(sizeDec)} ${position.symbol}` : ''} {phase.price != null ? `@ $${fmt(phase.price, priceDec)}` : ''} · order #{phase.orderId}
            </div>
            {phase.triggerNote && <div className="text-text-secondary mt-0.5">{phase.triggerNote}</div>}
          </div>
        )}
        {phase.kind === 'resting' && (
          <div className="p-3 bg-accent/10 border border-accent/30 rounded-lg text-xs">
            <div className="font-semibold text-accent">Limit order resting in the Perpl book</div>
            <div className="text-text-secondary mt-0.5">order #{phase.orderId} · TP/SL (if set) must be added after it fills — no position exists yet.</div>
          </div>
        )}
        {phase.kind === 'rejected' && (
          <div className="p-3 bg-danger/10 border border-danger/30 rounded-lg text-xs">
            <div className="font-semibold text-danger">Not executed</div>
            <div className="text-text-secondary mt-0.5">{phase.reason}</div>
          </div>
        )}
        {blocked && phase.kind !== 'rejected' && <div className="px-3 py-2 rounded-lg bg-danger/10 border border-danger/25 text-xs text-danger">{blocked}</div>}
        {status && (phase.kind === 'placing' || phase.kind === 'authorizing') && <div className="text-xs text-accent animate-pulse">{status}</div>}

        {/* Actions */}
        <div className="flex items-center gap-2">
          <button type="button" onClick={onClose} disabled={phase.kind === 'placing'} className="flex-1 text-sm font-medium py-2.5 rounded-lg bg-bg-secondary text-text-secondary hover:text-text-primary transition-colors">
            {phase.kind === 'filled' || phase.kind === 'resting' ? 'Done' : 'Cancel'}
          </button>
          {!isAuthenticated ? (
            <button type="button" onClick={() => { login().then(() => { pullStatus(); }).catch(() => {}); }}
              disabled={authLoading}
              className="flex-1 text-sm font-semibold py-2.5 rounded-lg bg-accent text-white hover:bg-accent/80 transition-colors">
              {authLoading ? 'Signing in…' : 'Sign in to enable'}
            </button>
          ) : (
            <button
              type="button"
              onPointerDown={startHold}
              onPointerUp={stopHold}
              onPointerLeave={stopHold}
              onPointerCancel={stopHold}
              onContextMenu={(e) => e.preventDefault()}
              disabled={!canPlace}
              className={clsx(
                'relative overflow-hidden flex-1 text-sm font-semibold py-2.5 rounded-lg transition-colors select-none touch-none',
                position.side === 'long' ? 'bg-success text-white' : 'bg-danger text-white',
                !canPlace && 'opacity-50 cursor-not-allowed',
              )}
            >
              {/* hold progress fill */}
              <span className="absolute inset-y-0 left-0 bg-white/25 pointer-events-none" style={{ width: `${holdPct}%` }} />
              <span className="relative">
                {phase.kind === 'placing' || phase.kind === 'authorizing' || isCopying ? 'Placing…'
                  : holdPct > 0 ? 'Keep holding…'
                  : `Hold to ${orderMode === 'limit' ? 'place limit' : 'market'} ${position.side.toUpperCase()} ${position.symbol} ${leverage}x`}
              </span>
            </button>
          )}
        </div>
        {disableReason && isAuthenticated && (
          <div className="text-[11px] text-warning -mt-1">{disableReason}</div>
        )}
        {!isAuthenticated && (
          <div className="text-[11px] text-warning -mt-1">Sign in with your wallet to enable live copy.</div>
        )}
      </div>
    </Modal>
  );
}

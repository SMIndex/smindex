import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { MARKET_CONFIGS, type MarketConfig } from '@/lib/perplTrading';

function getConfig(market_id: number): MarketConfig {
  // Fallback only if live configs haven't loaded. Fee is in BPS (6.9 = Tier 1
  // taker) — the old fallback of 500 was the RAW hundredths-of-a-bps encoding
  // and produced a 5% paper fee.
  return MARKET_CONFIGS[market_id] ?? {
    sizeDecimals: 5, priceDecimals: 1, initialMarginHdths: 1000,
    maintenanceMarginHdths: 2000, maxLeverage: 10, takerFeeBps: 6.9,
    orderTtlBlocks: 6, maxPriceImpactPct: 5, maxMarketSlippageBps: 100,
    maxTriggerOrders: 16, fundingIntervalSec: 3600,
  };
}

export interface PaperPosition {
  id: string;
  market_id: number;
  symbol: string;
  side: 'long' | 'short';
  size: number;           // in coin units
  entry_price: number;    // fill price (ask for longs, bid for shorts)
  leverage: number;
  deposit: number;        // margin in USD
  notional: number;       // size * entry_price
  fee_paid: number;       // taker fee on entry
  premium_pnl: number;    // accumulated funding payments
  last_funding_ts: number;
  timestamp: number;
}

export interface PaperTrade {
  id: string;
  market_id: number;
  symbol: string;
  side: 'long' | 'short';
  size: number;
  entry_price: number;
  exit_price: number;
  leverage: number;
  deposit: number;
  delta_pnl: number;      // price pnl
  premium_pnl: number;    // funding pnl
  total_pnl: number;      // delta + premium - close fee
  roe: number;
  entry_fee: number;
  exit_fee: number;
  opened_at: number;
  closed_at: number;
}

/**
 * Perpl-matching calculations:
 *
 * Fill price: longs fill at ask, shorts fill at bid (taker)
 * Taker fee: notional * (takerFeeBps / 10000)
 * Delta PnL: side * (mark - entry) * size  (side: +1 long, -1 short)
 * Premium PnL: funding rate applied per funding_interval
 * Liq price (from SDK):
 *   For long:  entry - (deposit + premium_pnl - MMR) / size
 *   For short: entry + (deposit + premium_pnl - MMR) / size
 *   where MMR = notional * (100 / maintenanceMarginHdths)
 *
 *   Simplified: liq triggers when deposit + total_pnl <= MMR
 */

function calcLiqPrice(pos: PaperPosition): number {
  const mcfg = getConfig(pos.market_id);
  // MMR as fraction of notional at entry
  const mmrFraction = 100 / mcfg.maintenanceMarginHdths; // BTC:2000→5%, MON:1000→10%
  const mmr = pos.notional * mmrFraction;

  if (pos.side === 'long') {
    // liq when: deposit + premium_pnl + (mark - entry) * size = mmr
    // mark = entry - (deposit + premium_pnl - mmr) / size
    return pos.entry_price - (pos.deposit + pos.premium_pnl - mmr) / pos.size;
  } else {
    return pos.entry_price + (pos.deposit + pos.premium_pnl - mmr) / pos.size;
  }
}

function calcDeltaPnl(pos: PaperPosition, markPrice: number): number {
  return pos.side === 'long'
    ? (markPrice - pos.entry_price) * pos.size
    : (pos.entry_price - markPrice) * pos.size;
}

function isLiquidated(pos: PaperPosition, markPrice: number): boolean {
  const liqPrice = calcLiqPrice(pos);
  if (pos.side === 'long') return markPrice <= liqPrice;
  return markPrice >= liqPrice;
}

interface PaperStoreState {
  balance: number;
  starting_balance: number;
  positions: PaperPosition[];
  history: PaperTrade[];

  resetAccount: (balance?: number) => void;
  openPosition: (params: {
    market_id: number;
    symbol: string;
    side: 'long' | 'short';
    amount_usd: number;
    leverage: number;
    bid_price: number;
    ask_price: number;
    mark_price: number;
  }) => string | null;
  closePosition: (positionId: string, bid_price: number, ask_price: number) => string | null;
  applyFunding: (market_id: number, funding_rate: number) => void;
  checkLiquidations: (market_id: number, mark_price: number) => void;

  // Getters for UI
  calcLiqPrice: typeof calcLiqPrice;
  calcDeltaPnl: typeof calcDeltaPnl;
}

export const usePaperStore = create<PaperStoreState>()(
  persist(
    (set, get) => ({
      balance: 10000,
      starting_balance: 10000,
      positions: [],
      history: [],

      calcLiqPrice,
      calcDeltaPnl,

      resetAccount: (bal = 10000) =>
        set({ balance: bal, starting_balance: bal, positions: [], history: [] }),

      openPosition: (params) => {
        const state = get();
        const mcfg = getConfig(params.market_id);

        if (params.leverage < 1 || params.leverage > mcfg.maxLeverage) {
          return `Leverage must be 1-${mcfg.maxLeverage}x for ${params.symbol}`;
        }
        if (params.amount_usd <= 0) return 'Amount must be > 0';

        // Fill at ask for longs, bid for shorts (taker order)
        const fillPrice = params.side === 'long' ? params.ask_price : params.bid_price;
        if (fillPrice <= 0) return 'Invalid market price';

        const notional = params.amount_usd * params.leverage;
        const size = notional / fillPrice;
        const takerFee = notional * (mcfg.takerFeeBps / 10000);
        const totalCost = params.amount_usd + takerFee;

        if (totalCost > state.balance) {
          return `Need ${totalCost.toFixed(2)} (margin + fee) but only ${state.balance.toFixed(2)} available`;
        }

        // Check existing position on same market
        const existing = state.positions.find((p) => p.market_id === params.market_id);
        if (existing) {
          return `Already have an open ${existing.symbol} position. Close it first.`;
        }

        const position: PaperPosition = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          market_id: params.market_id,
          symbol: params.symbol,
          side: params.side,
          size,
          entry_price: fillPrice,
          leverage: params.leverage,
          deposit: params.amount_usd,
          notional,
          fee_paid: takerFee,
          premium_pnl: 0,
          last_funding_ts: Date.now(),
          timestamp: Date.now(),
        };

        set({
          balance: state.balance - totalCost,
          positions: [...state.positions, position],
        });

        return null;
      },

      closePosition: (positionId, bid_price, ask_price) => {
        const state = get();
        const pos = state.positions.find((p) => p.id === positionId);
        if (!pos) return 'Position not found';

        // Close at bid for longs (selling), ask for shorts (buying back).
        // Perpl charges fees on the OPEN side only — closing is free (matches
        // the live fee model in lib/perplFees: PERPL_CLOSE_FEE_BPS = 0).
        const exitPrice = pos.side === 'long' ? bid_price : ask_price;
        const closeFee = 0;

        const deltaPnl = calcDeltaPnl(pos, exitPrice);
        const totalPnl = deltaPnl + pos.premium_pnl - closeFee;
        const returnAmount = pos.deposit + deltaPnl + pos.premium_pnl - closeFee;
        const roe = pos.deposit > 0 ? (totalPnl / pos.deposit) * 100 : 0;

        const trade: PaperTrade = {
          id: pos.id,
          market_id: pos.market_id,
          symbol: pos.symbol,
          side: pos.side,
          size: pos.size,
          entry_price: pos.entry_price,
          exit_price: exitPrice,
          leverage: pos.leverage,
          deposit: pos.deposit,
          delta_pnl: deltaPnl,
          premium_pnl: pos.premium_pnl,
          total_pnl: totalPnl,
          roe,
          entry_fee: pos.fee_paid,
          exit_fee: closeFee,
          opened_at: pos.timestamp,
          closed_at: Date.now(),
        };

        set({
          balance: state.balance + Math.max(0, returnAmount),
          positions: state.positions.filter((p) => p.id !== positionId),
          history: [trade, ...state.history].slice(0, 100),
        });

        return null;
      },

      // Apply funding rate to open positions (called periodically)
      applyFunding: (market_id, funding_rate) => {
        const state = get();
        const now = Date.now();
        const updated = state.positions.map((pos) => {
          if (pos.market_id !== market_id) return pos;
          // Only apply once per funding interval (3600s)
          if (now - pos.last_funding_ts < 3600000) return pos;

          // Positive rate: longs pay shorts
          // payment = funding_rate * position_value
          const posValue = pos.size * pos.entry_price;
          const payment = funding_rate * posValue;
          const sign = pos.side === 'long' ? -1 : 1;

          return {
            ...pos,
            premium_pnl: pos.premium_pnl + sign * payment,
            last_funding_ts: now,
          };
        });
        set({ positions: updated });
      },

      // Auto-liquidate positions that hit liq price
      checkLiquidations: (market_id, mark_price) => {
        const state = get();
        const toLiquidate: PaperPosition[] = [];
        const remaining: PaperPosition[] = [];

        for (const pos of state.positions) {
          if (pos.market_id === market_id && isLiquidated(pos, mark_price)) {
            toLiquidate.push(pos);
          } else {
            remaining.push(pos);
          }
        }

        if (toLiquidate.length === 0) return;

        const newHistory = [...state.history];
        for (const pos of toLiquidate) {
          const liqPrice = calcLiqPrice(pos);
          const deltaPnl = calcDeltaPnl(pos, liqPrice);
          newHistory.unshift({
            id: pos.id,
            market_id: pos.market_id,
            symbol: pos.symbol,
            side: pos.side,
            size: pos.size,
            entry_price: pos.entry_price,
            exit_price: liqPrice,
            leverage: pos.leverage,
            deposit: pos.deposit,
            delta_pnl: deltaPnl,
            premium_pnl: pos.premium_pnl,
            total_pnl: -(pos.deposit), // lose entire deposit on liquidation
            roe: -100,
            entry_fee: pos.fee_paid,
            exit_fee: 0,
            opened_at: pos.timestamp,
            closed_at: Date.now(),
          });
        }

        set({
          positions: remaining,
          history: newHistory.slice(0, 100),
          // No balance return on liquidation — deposit is lost
        });
      },
    }),
    { name: 'perpl-paper-trading' },
  ),
);

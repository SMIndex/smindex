import { oneAtATime } from '@/lib/oneAtATime';
import { useState } from 'react';
import { clsx } from 'clsx';

import { triggerPrice, triggersFor, type AccountPosition, type AccountState } from '@/lib/accountStore';
import { formatPrice, formatUSD } from '@/lib/formatters';
import { MARKETS } from '@/config/constants';
import { mobileConfirm } from '@/lib/mobileConfirm';
import { closePosition, requireMarketConfig } from '@/lib/perplTrading';
import SlTpEditor from '@/components/common/SlTpEditor';
import { useToast } from '@/components/common/Toast';

/**
 * Open positions on the main mobile trade screen, with no tap needed.
 *
 * The owner's report: "the mobile trade terminal does not show my position
 * without tapping through tabs". Positions lived only behind the Positions
 * tab, so on a phone the one thing you most need while a trade is on was the
 * one thing you could not see. This card sits directly under the price strip,
 * above the chart and the order form, and carries everything that matters:
 * side, size, entry, mark, P/L, liquidation price and distance, the armed
 * SL/TP, and Close / SL-TP one tap away.
 *
 * It reads the shared account store, so it is the same position the Terminal
 * tab, the Portfolio and the Copy dashboard show, at the same moment. A row
 * the venue has reported but no chain read has confirmed yet is marked
 * "confirming" rather than hidden.
 */
export default function MobilePositionCards({ account, ensureConnected, onlyMarket }: {
  account: AccountState;
  ensureConnected: () => Promise<boolean>;
  /** Show just this market (the one being traded) first; others stay listed. */
  onlyMarket?: number;
}) {
  const toast = useToast();
  const [slTpFor, setSlTpFor] = useState<number | null>(null);
  const [closing, setClosing] = useState<number | null>(null);

  const positions = [...account.positions].sort((a, b) => (
    (b.marketId === onlyMarket ? 1 : 0) - (a.marketId === onlyMarket ? 1 : 0)
  ));

  const close = (p: AccountPosition) => oneAtATime(`close:${p.marketId}`, () => closeOnce(p));
  const closeOnce = async (p: AccountPosition) => {
    let mcfg;
    try {
      mcfg = requireMarketConfig(p.marketId);
    } catch (e: any) {
      toast.error(e?.message || 'Market settings not loaded');
      return;
    }
    const tx = await mobileConfirm({
      title: `Close ${p.symbol} ${p.side}`,
      confirmLabel: 'Close on Perpl',
      danger: true,
      rows: [
        ['Position', `${p.side} ${p.size} ${p.symbol}${p.leverage ? ` · ${p.leverage.toFixed(1)}x` : ''}`],
        ['Mark price', formatPrice(p.markPrice, MARKETS[p.marketId]?.decimals ?? 2)],
        ['Unrealized P/L', `${p.pnl >= 0 ? '+' : ''}${formatUSD(p.pnl)}`],
        ['Closing', '100% at market'],
      ],
    });
    if (!tx.ok) return;
    if (!(await ensureConnected())) { tx.fail('Not connected to Perpl'); return; }
    setClosing(p.marketId);
    try {
      await closePosition({
        marketId: p.marketId, side: p.side, size: p.size,
        priceDecimals: mcfg.priceDecimals, sizeDecimals: mcfg.sizeDecimals, markPrice: p.markPrice,
      });
      tx.done(`Closed ${p.symbol} ${p.side}`);
      toast.success(`Closed ${p.symbol} ${p.side}`);
      window.dispatchEvent(new CustomEvent('position_closed'));
    } catch (e: any) {
      tx.fail(e?.message || 'Close failed');
      toast.error(e?.message || 'Close failed');
    } finally {
      setClosing(null);
    }
  };

  if (!positions.length && account.noPerplAccount) {
    return (
      <div className="mpos none" data-testid="mobile-positions-empty">
        No Perpl account yet. <a href="https://app.perpl.xyz" target="_blank" rel="noopener noreferrer"
          style={{ color: 'inherit', textDecoration: 'underline' }}>Deposit on Perpl</a> to start trading
      </div>
    );
  }

  if (!positions.length) {
    // One line, not an empty box: nothing to shout about when flat.
    return (
      <div className="mpos none" data-testid="mobile-positions-empty">
        No open position{account.chainOrders.length ? ` · ${account.chainOrders.length} resting order${account.chainOrders.length > 1 ? 's' : ''}` : ''}
      </div>
    );
  }

  return (
    <div className="mposwrap" data-testid="mobile-positions">
      {positions.map((p) => {
        const dec = MARKETS[p.marketId]?.decimals ?? 2;
        const { sl, tp } = triggersFor(account, p.marketId, p.side);
        const slPx = sl ? triggerPrice(sl) : null;
        const tpPx = tp ? triggerPrice(tp) : null;
        const up = p.pnl >= 0;
        return (
          <div key={`${p.marketId}:${p.side}`} className="mpos">
            <div className="mpos-top">
              <span className="sym">{p.symbol}</span>
              <span className={clsx('sidetag', p.side === 'long' ? 'long' : 'short')}>
                {p.side === 'long' ? 'Long' : 'Short'}
              </span>
              {p.leverage > 0 && <span className="lev">{p.leverage.toFixed(p.leverage < 10 ? 1 : 0)}x</span>}
              {p.source === 'venue' && (
                <span className="confirming" title="Perpl has reported this fill; the chain read has not caught up yet.">
                  confirming
                </span>
              )}
              <b className={clsx('pnl', up ? 'long-c' : 'short-c')}>
                {up ? '+' : ''}{formatUSD(p.pnl)}
                {p.deposit > 0 && <i>{` ${up ? '+' : ''}${p.roePct.toFixed(1)}%`}</i>}
              </b>
            </div>

            <div className="mpos-grid">
              <div><span>Size</span><b>{p.size}</b></div>
              <div><span>Entry</span><b>{formatPrice(p.entryPrice, dec)}</b></div>
              <div><span>Mark</span><b>{formatPrice(p.markPrice, dec)}</b></div>
              <div>
                <span>Liquidation</span>
                <b className={p.liqDistancePct != null && p.liqDistancePct < 10 ? 'short-c' : undefined}>
                  {p.liqPrice == null ? '-' : formatPrice(p.liqPrice, dec)}
                  {p.liqDistancePct != null && <i>{` ${p.liqDistancePct.toFixed(1)}% away`}</i>}
                </b>
              </div>
            </div>

            <div className="mpos-legs">
              <span className={clsx('leg', sl ? 'on' : 'off')}>
                {sl ? `SL ${slPx != null ? formatPrice(slPx, dec) : 'set'}` : 'no SL'}
              </span>
              <span className={clsx('leg', tp ? 'on tp' : 'off')}>
                {tp ? `TP ${tpPx != null ? formatPrice(tpPx, dec) : 'set'}` : 'no TP'}
              </span>
              {!account.venueSession && <span className="leg off">connect to see SL/TP</span>}
            </div>

            <div className="mpos-act">
              <button
                className="btn sm"
                onClick={() => setSlTpFor(slTpFor === p.marketId ? null : p.marketId)}
                aria-expanded={slTpFor === p.marketId}
              >
                {sl || tp ? 'Edit SL / TP' : 'Set SL / TP'}
              </button>
              <button className="btn sm danger" disabled={closing === p.marketId} onClick={() => close(p)}>
                {closing === p.marketId ? 'Closing…' : 'Close'}
              </button>
            </div>

            {slTpFor === p.marketId && (
              <div className="mpos-sltp">
                <SlTpEditor
                  account={account}
                  ensureConnected={ensureConnected}
                  position={{
                    marketId: p.marketId, symbol: p.symbol, side: p.side,
                    size: p.size, markPrice: p.markPrice, leverage: p.leverage,
                  }}
                  compact
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

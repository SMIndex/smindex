import { useEffect, useMemo, useRef, useState } from 'react';
import StartPaperCopyModal from '@/components/copy/StartPaperCopyModal';
import { useSubscriptions } from '@/hooks/useCopyV1';
import { useAuth } from '@/hooks/useAuth';
import { getReplay, type FeaturedRow, type Replay, type ReplayDefaults } from '@/lib/featuredApi';
import { holdWords, pct, shortAddr, usd } from '@/lib/featuredView';
import { VenueBadge, LabelChips } from './bits';

// Copy sheet (design/CopySheet.dc.html): Discover's StartPaperCopyModal
// UNCHANGED in its money path, with the decision helpers rendered above its
// fields and the suggested defaults prefilled. No new order path.

const MARGINS = [10, 25, 50, 100];
const COPY_DELAY_SEC = 2;

export default function FeaturedCopySheet({ row, defaults, replay: initial, onClose }: {
  row: FeaturedRow; defaults: ReplayDefaults | null; replay: Replay | null; onClose: () => void;
}) {
  const { isAuthenticated } = useAuth();
  const subs = useSubscriptions(isAuthenticated);
  const [margin, setMargin] = useState<number>(defaults?.margin_usd ?? 50);
  const [custom, setCustom] = useState(false);
  const [lev, setLev] = useState<number>(defaults?.leverage ?? 3);
  const [stopOn, setStopOn] = useState(true);
  const [stop, setStop] = useState<number>(defaults?.suggested_stop_pct ?? defaults?.stop_pct ?? 1);
  const [maxOpen, setMaxOpen] = useState<number>(defaults?.max_open ?? 2);
  const [rep, setRep] = useState<Replay | null>(initial);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    const n = ++seq.current;
    setLoading(true);
    const t = setTimeout(() => {
      getReplay(row.venue, row.wallet, margin, lev, stopOn ? stop : null)
        .then((r) => { if (seq.current === n) setRep(r); })
        .catch(() => {})
        .finally(() => { if (seq.current === n) setLoading(false); });
    }, 250);
    return () => clearTimeout(t);
  }, [row.venue, row.wallet, margin, lev, stop, stopOn]);

  const speed = useMemo(() => {
    const h = row.median_hold_sec ?? 0;
    if (h < 300) return { word: 'Too fast', cls: 'neg', sub: `hold ${holdWords(h)} vs copy delay ${COPY_DELAY_SEC} s: too fast to copy well` };
    if (h < 1800) return { word: 'Tight', cls: '', sub: `hold ${holdWords(h)} vs copy delay ${COPY_DELAY_SEC} s` };
    return { word: 'Good', cls: 'pos', sub: `hold ${holdWords(h)} vs copy delay ${COPY_DELAY_SEC} s` };
  }, [row.median_hold_sec]);

  const extra = (
    <div className="ft-sheet">
      <div>
        <div className="lab">Copy trader</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }} className="ft">
          <VenueBadge venue={row.venue} />
          <span style={{ fontFamily: 'var(--mono, monospace)', fontSize: 18, fontWeight: 700 }}>{shortAddr(row.wallet)}</span>
          <LabelChips label={row.label} oneBigWin={row.one_big_win} />
        </div>
        {row.verdict && <div style={{ fontSize: 13, lineHeight: 1.5, marginTop: 8 }}>{row.verdict}</div>}
      </div>

      <div className="replay">
        <div className="lab">If you had copied this wallet at the settings below, last 90 days</div>
        <div className="big">
          {rep ? <span className={rep.pnl >= 0 ? 'pos' : 'neg'}>{usd(rep.pnl, true)}</span> : <span className="sub">computing…</span>}
          <span style={{ fontSize: 14, fontWeight: 500, opacity: .75 }}> on ${margin} margin{loading ? ' …' : ''}</span>
        </div>
        {rep && (
          <div className="facts">
            <span><span className="sub">Worst dip</span> <b>{usd(rep.worst_dip)}</b></span>
            <span><span className="sub">Stopped out</span> <b>{rep.stopped_out} of {rep.trades}</b></span>
            <span><span className="sub">Skipped</span> <b>{rep.skipped}</b> <span className="sub">{rep.skipped ? '(coins not on Perpl)' : '(all coins on Perpl)'}</span></span>
            <span><span className="sub">Fees</span> <b>{usd(rep.fees)}</b></span>
          </div>
        )}
        <div className="sub" style={{ marginTop: 6 }}>Recomputes as you change the settings. Entry 1.5 s after the trader's fill, Perpl taker fee included. Not a guarantee.</div>
      </div>

      <div className="ind">
        <div><div className="lab">Copiable</div><b>{row.copiable_pct != null ? pct(row.copiable_pct) : 'n/a'}</b><div className="sub">of profit on Perpl coins</div></div>
        <div><div className="lab">Speed fit</div><b className={speed.cls}>{speed.word}</b><div className="sub">{speed.sub}</div></div>
        <div><div className="lab">Losing streak</div><b>{row.longest_loss_streak ?? 'n/a'}</b><div className="sub">longest in 90 d</div></div>
      </div>

      <div>
        <div className="lab">Margin per trade</div>
        <div className="seg">
          {MARGINS.map((m) => <button key={m} type="button" aria-pressed={!custom && margin === m} onClick={() => { setCustom(false); setMargin(m); }}>${m}</button>)}
          <button type="button" aria-pressed={custom} onClick={() => setCustom(true)}>Custom</button>
        </div>
        {custom && <div className="row"><span className="sub">Custom margin (USD)</span><input type="number" min={1} step={1} value={margin} onChange={(e) => setMargin(Math.max(1, Number(e.target.value) || 1))} /></div>}
      </div>

      <div>
        <div className="lab">Leverage <span style={{ fontFamily: 'var(--mono, monospace)', color: '#2447E6', marginLeft: 6, textTransform: 'none' }}>{lev}x</span>
          {defaults?.trader_median_leverage != null && <span className="sub" style={{ textTransform: 'none', marginLeft: 6 }}>suggested: trader's median is {defaults.trader_median_leverage}x</span>}
        </div>
        <input type="range" min={1} max={12} step={1} value={lev} onChange={(e) => setLev(Number(e.target.value))} aria-label="Leverage" />
        <div className="sub" style={{ display: 'flex', justifyContent: 'space-between' }}><span>1x</span><span>12x</span></div>
      </div>

      <div>
        <div className="row">
          <div>
            <div style={{ fontWeight: 600 }}>Stop loss on every copy</div>
            <div className="sub">
              {defaults?.suggested_stop_pct != null && defaults?.stop_pct != null
                ? `Suggested ${defaults.suggested_stop_pct}% of entry (trader's average loss ${defaults.stop_pct}% plus a margin)`
                : 'Percent of entry price'}
            </div>
          </div>
          <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="number" min={0.1} step={0.1} value={stop} disabled={!stopOn} onChange={(e) => setStop(Math.max(0.1, Number(e.target.value) || 0.1))} aria-label="Stop loss percent" />
            <input type="checkbox" checked={stopOn} onChange={(e) => setStopOn(e.target.checked)} aria-label="Stop loss on" />
          </span>
        </div>
        <div className="row">
          <div>
            <div style={{ fontWeight: 600 }}>Max open copies from this trader</div>
            <div className="sub">{row.usual_open != null ? `Trader usually holds ${row.usual_open} to ${row.usual_open + 1} at once` : 'Usual number of open positions unknown'}</div>
          </div>
          <input type="number" min={1} max={20} step={1} value={maxOpen} onChange={(e) => setMaxOpen(Math.max(1, Number(e.target.value) || 1))} aria-label="Max open copies" />
        </div>
      </div>

      <div style={{ background: 'rgba(128,128,128,.08)', borderRadius: 10, padding: '10px 14px', fontSize: 13 }}>
        <div className="row"><span className="sub">Position size per copy</span><span style={{ fontFamily: 'var(--mono, monospace)' }}>${(margin * lev).toLocaleString('en-US')}</span></div>
        <div className="row"><span className="sub">Max margin in use</span><span style={{ fontFamily: 'var(--mono, monospace)' }}>${(margin * maxOpen).toLocaleString('en-US')}</span></div>
        <div className="row"><span className="sub">Each order</span><span>you confirm on Perpl</span></div>
        <div className="row"><span className="sub">Mode</span><span>Manual copy</span></div>
      </div>
      <div className="sub">The fields below are Discover's copy settings, prefilled from the choices above. Trading perps is risky. Copying can lose money. Past results do not predict future results.</div>
    </div>
  );

  return (
    <StartPaperCopyModal
      isOpen
      onClose={onClose}
      traderWallet={row.wallet}
      traderName={row.display_name}
      exchange={row.venue}
      onSubmit={subs.create}
      extra={extra}
      defaults={{
        max_margin_per_trade: String(margin),
        max_leverage: String(Math.min(20, Math.max(1, lev))),
        allocation_usd: String(margin * maxOpen),
        max_daily_loss: String(Math.max(1, Math.round(margin * maxOpen * 0.5))),
        max_total_loss: String(Math.max(1, Math.round(margin * maxOpen * 2))),
      }}
      title="Copy trader"
    />
  );
}

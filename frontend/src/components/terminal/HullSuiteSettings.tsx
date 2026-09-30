import { useState, useEffect, useRef } from 'react';
import type { HullSuiteSettings } from '@/lib/hullSuite';
import { DEFAULT_HULL_SETTINGS } from '@/lib/hullSuite';
import type { QqexSettings } from '@/lib/qqex';
import { clsx } from 'clsx';

interface Props {
  open: boolean;
  onClose: () => void;
  settings: HullSuiteSettings;
  onChange: (s: HullSuiteSettings) => void;
}

export default function HullSuitePanel({ open, onClose, settings, onChange }: Props) {
  const [local, setLocal] = useState<HullSuiteSettings>(settings);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => setLocal(settings), [settings]);

  // Click-outside close
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    setTimeout(() => document.addEventListener('mousedown', handler), 0);
    return () => document.removeEventListener('mousedown', handler);
  }, [open, onClose]);

  if (!open) return null;

  const update = <K extends keyof HullSuiteSettings>(k: K, v: HullSuiteSettings[K]) => {
    const next = { ...local, [k]: v };
    setLocal(next);
    onChange(next);
  };
  const updateQqex = <K extends keyof QqexSettings>(k: K, v: QqexSettings[K]) =>
    update('qqex', { ...local.qqex, [k]: v });
  const isQqex = local.strategyVariant === 'qqex_v6';

  const reset = () => {
    setLocal(DEFAULT_HULL_SETTINGS);
    onChange(DEFAULT_HULL_SETTINGS);
  };

  return (
    <div
      ref={ref}
      className="absolute z-50 top-12 right-3 w-[340px] max-h-[80vh] overflow-y-auto bg-bg-primary border border-text-secondary/20 rounded-lg shadow-2xl text-xs"
    >
      <div className="sticky top-0 z-10 flex items-center justify-between px-3 py-2 border-b border-text-secondary/10 bg-bg-primary">
        <span className="font-semibold text-text-primary">{isQqex ? 'QQEX v6.0 Settings' : 'Hull Suite Settings'}</span>
        <div className="flex items-center gap-2">
          <button onClick={reset} className="text-[10px] text-text-secondary hover:text-accent transition-colors">Reset</button>
          <button onClick={onClose} className="text-text-secondary hover:text-text-primary">✕</button>
        </div>
      </div>

      <div className="p-3 space-y-3">
        <Section title="Strategy">
          <Select label="Variant" value={local.strategyVariant} onChange={(v) => update('strategyVariant', v as any)}
            options={[['entry_suite', 'Hull Entry Suite (filtered)'], ['crossover', 'Hull Suite Strategy (crossover)'], ['qqex_v6', 'QQEX v6.0 (QQE Cross)']]} />
          <Toggle label="Show Trade Boxes" value={local.showTradeBoxes} onChange={(v) => update('showTradeBoxes', v)} />
          {local.showTradeBoxes && (<>
            {/* QQEX has its own exits (close signals) — boxes are always entry→exit P&L */}
            {!isQqex && (
              <Select label="Box Type" value={local.boxMode} onChange={(v) => update('boxMode', v as any)}
                options={[['target', 'Target + Stop per trade (ATR)'], ['backtest', 'Trade P&L']]} />
            )}
            <NumInput label="Max Boxes" value={local.maxBoxes} onChange={(v) => update('maxBoxes', v)} min={1} max={50} step={1} />
            {!isQqex && local.boxMode === 'target' && (<>
              <NumInput label="ATR Length" value={local.atrLen} onChange={(v) => update('atrLen', v)} min={2} max={100} step={1} />
              <NumInput label="Target × ATR" value={local.atrTargetMult} onChange={(v) => update('atrTargetMult', v)} min={0.5} max={10} step={0.5} />
              <NumInput label="Stop × ATR" value={local.atrStopMult} onChange={(v) => update('atrStopMult', v)} min={0.5} max={10} step={0.5} />
            </>)}
          </>)}
        </Section>

        {isQqex && (<>
          <Section title="QQE Core">
            <NumInput label="RSI Length" value={local.qqex.rsiLen} onChange={(v) => updateQqex('rsiLen', v)} min={2} max={50} step={1} />
            <NumInput label="RSI Smoothing (SF)" value={local.qqex.sf} onChange={(v) => updateQqex('sf', v)} min={1} max={30} step={1} />
            <NumInput label="QQE Factor" value={local.qqex.qqeFactor} onChange={(v) => updateQqex('qqeFactor', v)} min={0.5} max={20} step={0.1} />
            <NumInput label="RSI Threshold" value={local.qqex.threshold} onChange={(v) => updateQqex('threshold', v)} min={1} max={30} step={1} />
          </Section>
          <Section title="EMA Ribbons">
            <NumInput label="Fast EMA" value={local.qqex.fastLen} onChange={(v) => updateQqex('fastLen', v)} min={2} max={200} step={1} />
            <NumInput label="Medium EMA" value={local.qqex.medLen} onChange={(v) => updateQqex('medLen', v)} min={2} max={200} step={1} />
            <NumInput label="Slow EMA" value={local.qqex.slowLen} onChange={(v) => updateQqex('slowLen', v)} min={2} max={200} step={1} />
            <NumInput label="Anchor (×)" value={local.qqex.anchor} onChange={(v) => updateQqex('anchor', v)} min={1} max={10} step={1} />
            <Toggle label="Show Anchor Ribbon" value={local.qqex.showAltRibbon} onChange={(v) => updateQqex('showAltRibbon', v)} />
          </Section>
          <Section title="Signals & Filters">
            <Select label="Open Signal" value={local.qqex.tradeSignal} onChange={(v) => updateQqex('tradeSignal', v as any)}
              options={[['XC', 'XC — zone exit (60/40)'], ['XQ', 'XQ — QQE-line cross'], ['XZ', 'XZ (author: unreliable)']]} />
            <Toggle label="MA Ribbon Filter" value={local.qqex.useFilter} onChange={(v) => updateQqex('useFilter', v)} />
            <Toggle label="Directional Filter" value={local.qqex.useDfilter} onChange={(v) => updateQqex('useDfilter', v)} />
            {local.qqex.tradeSignal === 'XQ' && (
              <Toggle label="XQ Zone Filter" value={local.qqex.xfilter} onChange={(v) => updateQqex('xfilter', v)} />
            )}
          </Section>
          <Section title="Event Marks">
            <Toggle label="XC marks (zone exit)" value={local.qqex.showXc} onChange={(v) => updateQqex('showXc', v)} />
            <Toggle label="XQ marks (QQE cross)" value={local.qqex.showXq} onChange={(v) => updateQqex('showXq', v)} />
            <Toggle label="XZ marks (50 cross)" value={local.qqex.showXz} onChange={(v) => updateQqex('showXz', v)} />
          </Section>
          <button
            onClick={() => update('qqex', { ...DEFAULT_HULL_SETTINGS.qqex })}
            className="w-full px-2 py-1.5 rounded text-[11px] font-semibold bg-bg-secondary border border-text-secondary/10 text-text-secondary hover:text-accent hover:border-accent/40 transition-colors">
            Reset QQEX to defaults
          </button>
        </>)}

        {!isQqex && (<>
        <Section title="Hull">
          <Select label="Variation" value={local.hullMode} onChange={(v) => update('hullMode', v as any)}
            options={[['Hma', 'HMA'], ['Ehma', 'EHMA'], ['Thma', 'THMA']]} />
          <NumInput label="Length" value={local.hullLength} onChange={(v) => update('hullLength', v)} min={2} max={500} step={1} />
          <NumInput label="Length Multiplier" value={local.hullLengthMult} onChange={(v) => update('hullLengthMult', v)} min={0.1} max={5} step={0.1} />
          <Toggle label="Use Higher Timeframe Hull" value={local.useHtfHull} onChange={(v) => update('useHtfHull', v)} />
          {local.useHtfHull && (
            <Select label="Hull HTF (min)" value={local.hullHtf} onChange={(v) => update('hullHtf', v)}
              options={[['15', '15m'], ['60', '1h'], ['240', '4h'], ['1440', '1d']]} />
          )}
        </Section>

        <Section title="Display">
          <Toggle label="Color Hull by Trend" value={local.switchColor} onChange={(v) => update('switchColor', v)} />
          <Toggle label="Show as Band (MHULL + SHULL)" value={local.showBand} onChange={(v) => update('showBand', v)} />
          <NumInput label="Line Thickness" value={local.hullThickness} onChange={(v) => update('hullThickness', v)} min={1} max={5} step={1} />
          <NumInput label="Band Transparency" value={local.bandTransparency} onChange={(v) => update('bandTransparency', v)} min={0} max={100} step={5} />
        </Section>

        <Section title="VWAP Filter">
          <Toggle label="Use VWAP" value={local.useVWAP} onChange={(v) => update('useVWAP', v)} />
        </Section>

        <Section title="Volume Filter">
          <Toggle label="Use Volume" value={local.useVolume} onChange={(v) => update('useVolume', v)} />
          {local.useVolume && (<>
            <NumInput label="Volume SMA Length" value={local.volumeLen} onChange={(v) => update('volumeLen', v)} min={2} max={200} step={1} />
            <NumInput label="Volume Multiplier" value={local.volumeMult} onChange={(v) => update('volumeMult', v)} min={0.5} max={5} step={0.1} />
          </>)}
        </Section>

        <Section title="ADX Filter">
          <Toggle label="Use ADX" value={local.useADX} onChange={(v) => update('useADX', v)} />
          {local.useADX && (<>
            <NumInput label="ADX Length" value={local.adxLen} onChange={(v) => update('adxLen', v)} min={2} max={100} step={1} />
            <NumInput label="Minimum ADX" value={local.adxMin} onChange={(v) => update('adxMin', v)} min={5} max={60} step={1} />
          </>)}
        </Section>

        <Section title="Market Structure Filter">
          <Toggle label="Use Structure" value={local.useStructure} onChange={(v) => update('useStructure', v)} />
          {local.useStructure && (
            <NumInput label="Structure Lookback" value={local.structureLen} onChange={(v) => update('structureLen', v)} min={2} max={200} step={1} />
          )}
        </Section>

        <Section title="Support / Resistance Filter">
          <Toggle label="Use S/R" value={local.useSR} onChange={(v) => update('useSR', v)} />
          {local.useSR && (<>
            <NumInput label="S/R Lookback" value={local.srLen} onChange={(v) => update('srLen', v)} min={5} max={500} step={1} />
            <Select label="Entry Mode" value={local.entryMode} onChange={(v) => update('entryMode', v as any)}
              options={[['Breakout', 'Breakout'], ['Pullback', 'Pullback']]} />
            <NumInput label="Pullback Buffer %" value={local.pullbackBuffer} onChange={(v) => update('pullbackBuffer', v)} min={0.05} max={5} step={0.05} />
          </>)}
        </Section>

        <Section title="Higher Timeframe Trend">
          <Toggle label="Use HTF Trend" value={local.useHTFTrend} onChange={(v) => update('useHTFTrend', v)} />
          {local.useHTFTrend && (<>
            <Select label="Trend HTF (min)" value={local.trendHtf} onChange={(v) => update('trendHtf', v)}
              options={[['15', '15m'], ['60', '1h'], ['240', '4h'], ['1440', '1d']]} />
            <NumInput label="HTF EMA Length" value={local.htfEmaLen} onChange={(v) => update('htfEmaLen', v)} min={20} max={500} step={1} />
          </>)}
        </Section>
        </>)}

        <Section title="Signals">
          <Toggle label="Show BUY/SELL Labels" value={local.showBuySell} onChange={(v) => update('showBuySell', v)} />
          <Toggle label="Show Entry Arrows" value={local.showArrows} onChange={(v) => update('showArrows', v)} />
          {!isQqex && (
            <NumInput label="Signal Cooldown (bars)" value={local.cooldownBars} onChange={(v) => update('cooldownBars', v)} min={0} max={200} step={1} />
          )}
        </Section>
      </div>
    </div>
  );
}

// ─── primitives ────────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-text-secondary/60 mb-1.5 font-semibold">{title}</div>
      <div className="space-y-1.5">{children}</div>
    </div>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center justify-between cursor-pointer hover:bg-bg-secondary/40 px-1.5 py-1 rounded">
      <span className="text-text-secondary text-[11px]">{label}</span>
      <button
        type="button"
        onClick={() => onChange(!value)}
        className={clsx('w-8 h-4 rounded-full transition-colors flex items-center', value ? 'bg-accent' : 'bg-text-secondary/20')}
      >
        <span className={clsx('w-3 h-3 rounded-full bg-white transition-transform', value ? 'translate-x-4' : 'translate-x-0.5')} />
      </button>
    </label>
  );
}

function NumInput({ label, value, onChange, min, max, step }: { label: string; value: number; onChange: (v: number) => void; min: number; max: number; step: number }) {
  return (
    <label className="flex items-center justify-between px-1.5 py-1">
      <span className="text-text-secondary text-[11px]">{label}</span>
      <input
        type="number"
        value={value}
        min={min} max={max} step={step}
        onChange={(e) => {
          const v = parseFloat(e.target.value);
          if (!isNaN(v) && v >= min && v <= max) onChange(v);
        }}
        className="w-20 px-2 py-1 text-right text-[11px] bg-bg-secondary border border-text-secondary/10 rounded focus:outline-none focus:border-accent"
      />
    </label>
  );
}

function Select<T extends string>({ label, value, onChange, options }: { label: string; value: T; onChange: (v: T) => void; options: [T, string][] }) {
  return (
    <label className="flex items-center justify-between px-1.5 py-1">
      <span className="text-text-secondary text-[11px]">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className="w-24 px-2 py-1 text-right text-[11px] bg-bg-secondary border border-text-secondary/10 rounded focus:outline-none focus:border-accent"
      >
        {options.map(([v, l]) => (<option key={v} value={v}>{l}</option>))}
      </select>
    </label>
  );
}

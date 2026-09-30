import { useEffect, useRef, useState, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  createChart, ColorType, CandlestickSeries, HistogramSeries,
  LineSeries, createSeriesMarkers, type IChartApi, type ISeriesApi,
} from 'lightweight-charts';
import { clsx } from 'clsx';
// Inline fetch wrappers — avoid importing from @/lib/api (circular dep with authStore)
// Throw on non-2xx so callers (pagination) can catch Perpl's 500 (range too large) and retry.
const getCandles = async (mkt: number, res: number, from: number, to: number) => {
  const r = await fetch(`/api/candles/${mkt}/${res}/${from}-${to}`);
  if (!r.ok) throw new Error(`candles ${r.status}`);
  return r.json();
};
import { useMarketStore } from '@/stores/marketStore';
import { useTradingStore } from '@/stores/tradingStore';
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import { detectAllPatterns } from '@/lib/patternDetection';
import { ema, sma, bollingerBands, rsi, macd, type Candle } from '@/lib/indicators';
import { computeHullSuite, computeHTFHullSrc, buildTradeBoxes, DEFAULT_HULL_SETTINGS, type HullSuiteSettings } from '@/lib/hullSuite';
import { computeQqex, DEFAULT_QQEX_SETTINGS, QQEX_WARMUP_BARS } from '@/lib/qqex';
import { QqexRibbonPrimitive } from '@/lib/qqexRibbonPrimitive';
import { candleBus } from '@/lib/candleBus';
import { HullBandPrimitive, type HullBandPoint } from '@/lib/hullBandPrimitive';
import { HullBoxPrimitive } from '@/lib/hullBoxPrimitive';
import HullSuitePanel from './HullSuiteSettings';
import { getChartColors, useTheme } from '@/hooks/useTheme';

// Perpl's candle API caps a single request at ~1030 bars (≥1040 -> HTTP 500). Fetch as
// many as it allows per timeframe (was only 90–360), so the chart holds real history to
// scroll through — matching Perpl. range = MAX_BARS * resolution.
const MAX_BARS = 1000;
// Bars shown by default (recent, readable); the rest are scrollable to the left.
const DEFAULT_VISIBLE_BARS = 160;
const TIMEFRAMES = [
  { label: '1m', resolution: 60 },
  { label: '5m', resolution: 300 },
  { label: '15m', resolution: 900 },
  { label: '1h', resolution: 3600 },
  { label: '4h', resolution: 14400 },
  { label: '1d', resolution: 86400 },
].map((t) => ({ ...t, range: MAX_BARS * t.resolution * 1000 }));

type IndicatorKey = 'ema9' | 'ema21' | 'sma50' | 'bb' | 'rsi' | 'macd' | 'hull';

interface HLineDrawing {
  id: string;
  price: number;
}

const IND_COLORS: Record<string, string> = {
  ema9: '#a78bfa', ema21: '#f59e0b', sma50: '#38bdf8',
  bb_upper: '#6366f180', bb_middle: '#6366f1', bb_lower: '#6366f180',
};

interface TradingChartProps {
  marketId: number;
  symbol: string;
  entryPrice?: number;
  liqPrice?: number;
}

// Design B chart palette. ONLY activates inside the B Terminal (a `.dsb .tbwrap`
// element in the DOM) — there it reads the B tokens off that element, so the
// canvas colors swap with the B light/dark toggle. Everywhere else (shell A) it
// returns getChartColors() plus the exact prior hardcoded hex → byte-identical.
function chartPalette() {
  const dsb = typeof document !== 'undefined'
    ? (document.querySelector('.dsb .tbwrap') as HTMLElement | null) : null;
  if (dsb) {
    const s = getComputedStyle(dsb);
    const g = (n: string, f: string) => (s.getPropertyValue(n).trim() || f);
    return {
      dsb: true,
      bg: g('--bg', '#0d1117'), grid: 'rgba(128,128,128,0.12)',
      border: g('--line', '#1c2128'), text: g('--muted', '#8b949e'),
      up: g('--long', '#3fb950'), down: g('--short', '#f85149'),
      accent: g('--accent', '#a78bfa'),
      entry: g('--accent', '#a78bfa'), liq: g('--short', '#f85149'),
      sigLong: g('--long', '#22c55e'), sigShort: g('--short', '#ef4444'), sigClose: g('--dim', '#9ca3af'),
    };
  }
  const cc = getChartColors();
  return {
    dsb: false,
    bg: cc.bg, grid: cc.grid, border: cc.border, text: cc.text,
    up: '#3fb950', down: '#f85149', accent: '#a78bfa', entry: '#a78bfa', liq: '#f85149',
    sigLong: '#22c55e', sigShort: '#ef4444', sigClose: '#9ca3af',
  };
}

export default function TradingChart({ marketId, symbol, entryPrice, liqPrice }: TradingChartProps) {
  const mainChartRef = useRef<HTMLDivElement>(null);
  const rsiChartRef = useRef<HTMLDivElement>(null);
  const macdChartRef = useRef<HTMLDivElement>(null);

  const chartRef = useRef<IChartApi | null>(null);
  const rsiChartObjRef = useRef<IChartApi | null>(null);
  const macdChartObjRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<any>(null);
  const volumeSeriesRef = useRef<any>(null);
  const indicatorSeriesRef = useRef<Map<string, ISeriesApi<any>>>(new Map());
  const hullBandRef = useRef<HullBandPrimitive | null>(null);
  const hullBandAttachedTo = useRef<any>(null);
  const hullBoxRef = useRef<HullBoxPrimitive | null>(null);
  const hullMarkersRef = useRef<any>(null);
  const qqexRibbonRef = useRef<QqexRibbonPrimitive | null>(null);      // primary EMA 16/21/26
  const qqexRibbonAltRef = useRef<QqexRibbonPrimitive | null>(null);   // anchor EMA 64/84/104
  const qqexAnchorTimeRef = useRef<number>(0);   // warmup anchor: bar-300 time of the INITIAL load
  const candlesRef = useRef<Candle[]>([]);
  const liveCandleRef = useRef<{ time: number; open: number; high: number; low: number; close: number } | null>(null);
  const lastWsCandleAtRef = useRef(0);   // last candles@ stream update (ms) — gates the mark-price fallback
  const [mainChartHeight, setMainChartHeight] = useState(420);

  // ----- scroll-back pagination state -----
  const rawCandlesRef = useRef<any[]>([]);        // merged raw payloads {t,o,h,l,c,v}, ascending by t (ms)
  const oldestTimeRef = useRef<number>(0);         // oldest loaded raw t (ms)
  const hasMoreHistoryRef = useRef(true);          // false once Perpl returns no older candles
  const loadingOlderRef = useRef(false);           // in-flight guard (no duplicate request)
  const lastOlderFromRef = useRef<number>(0);      // de-dupe identical older-range requests
  const followLiveRef = useRef(true);              // is the user pinned to the right (live) edge?
  const priceLinesRef = useRef<any[]>([]);         // entry/liq price-line handles (managed separately)
  const loadOlderRef = useRef<() => void>(() => {});
  const [candlesEpoch, setCandlesEpoch] = useState(0); // bumps on structural change (init / prepend / new bar)
  const [loadingOlder, setLoadingOlder] = useState(false); // subtle left-edge loader

  const resolution = useTradingStore((s) => s.resolution);
  const setResolution = useTradingStore((s) => s.setResolution);
  const setPatterns = useTradingStore((s) => s.setPatterns);
  const markets = useMarketStore((s) => s.markets);
  const { theme } = useTheme();
  const mcfg = MARKET_CONFIGS[marketId];
  const priceDec = mcfg?.priceDecimals ?? 1;
  const pd = 10 ** priceDec;

  const [activeIndicators, setActiveIndicators] = useState<Set<IndicatorKey>>(
    () => new Set(JSON.parse(localStorage.getItem('chart-indicators') || '["ema9","ema21"]')),
  );
  const [isDrawingHLine, setIsDrawingHLine] = useState(false);
  const [hlines, setHlines] = useState<HLineDrawing[]>(
    () => JSON.parse(localStorage.getItem(`hlines-${marketId}`) || '[]'),
  );
  const [hullSettings, setHullSettings] = useState<HullSuiteSettings>(() => {
    try {
      const stored = localStorage.getItem('hull-suite-settings');
      let s: HullSuiteSettings = stored ? { ...DEFAULT_HULL_SETTINGS, ...JSON.parse(stored) } : DEFAULT_HULL_SETTINGS;
      // One-time migration: the Entry Suite used to require all 7 filters (fired
      // almost never). Loosen to the core (Hull+VWAP+ADX) for existing users too.
      if (s.filtersV !== 2) {
        s = { ...s, useVolume: false, useStructure: false, useSR: false, useHTFTrend: false, filtersV: 2 };
      }
      // Nested QQEX params: deep-merge so new fields added later pick up defaults.
      s = { ...s, qqex: { ...DEFAULT_QQEX_SETTINGS, ...(s as any).qqex } };
      return s;
    } catch { return DEFAULT_HULL_SETTINGS; }
  });
  const [hullPanelOpen, setHullPanelOpen] = useState(false);
  const [hullMenuOpen, setHullMenuOpen] = useState(false);
  const [hullReadout, setHullReadout] = useState<{
    mhull: number | null; shull: number | null; vwap: number | null;
    resistance: number | null; support: number | null; htfEma: number | null;
    lastBull: boolean; signals: number;
  } | null>(null);
  const [qqexReadout, setQqexReadout] = useState<{
    rsindex: number | null; tl: number | null; trend: 1 | -1;
    state: 'flat' | 'long' | 'short'; openSignals: number;
  } | null>(null);

  useEffect(() => { localStorage.setItem('chart-indicators', JSON.stringify([...activeIndicators])); }, [activeIndicators]);
  useEffect(() => { localStorage.setItem(`hlines-${marketId}`, JSON.stringify(hlines)); }, [hlines, marketId]);
  useEffect(() => { localStorage.setItem('hull-suite-settings', JSON.stringify(hullSettings)); }, [hullSettings]);

  // ResizeObserver: measure the flex-1 main chart container to know its actual
  // pixel height. This lets lightweight-charts fill the space the flex layout
  // allocates (which shrinks automatically when RSI/MACD panes are active).
  useEffect(() => {
    const el = mainChartRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      const h = rect?.height;
      if (h && h > 50) setMainChartHeight(Math.floor(h));
      // Also track WIDTH — the container reflows wider when the order book /
      // place-order panels are hidden, and lightweight-charts otherwise only
      // resizes on a window resize event (so the canvas stayed narrow).
      const w = rect?.width;
      if (w && w > 50) {
        const width = Math.floor(w);
        try { chartRef.current?.applyOptions({ width }); } catch {}
        try { rsiChartObjRef.current?.applyOptions({ width }); } catch {}
        try { macdChartObjRef.current?.applyOptions({ width }); } catch {}
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const toggleIndicator = (key: IndicatorKey) => {
    setActiveIndicators((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const setHull = (patch: Partial<HullSuiteSettings>) => setHullSettings((s) => ({ ...s, ...patch }));
  const pickHullVariant = (v: HullSuiteSettings['strategyVariant']) => {
    setHull({ strategyVariant: v });
    setActiveIndicators((prev) => new Set(prev).add('hull'));
    setHullMenuOpen(false);
  };

  // Close the Hull strategy menu on outside click.
  useEffect(() => {
    if (!hullMenuOpen) return;
    const h = () => setHullMenuOpen(false);
    const t = setTimeout(() => document.addEventListener('click', h), 0);
    return () => { clearTimeout(t); document.removeEventListener('click', h); };
  }, [hullMenuOpen]);

  const tf = TIMEFRAMES.find((t) => t.resolution === resolution) || TIMEFRAMES[2];
  const now = Date.now();
  const from = now - tf.range;

  // Initial load only (per market/resolution). No 30s refetch — that did a full chart
  // rebuild which would wipe paginated history + reset the view. The live edge is kept
  // current by the WS forming-candle effect + the incremental recent-sync below.
  const { data } = useQuery({
    queryKey: ['candles', marketId, resolution],
    queryFn: () => getCandles(marketId, resolution, from, now),
    staleTime: Infinity,
  });

  // HTF candle queries — only fetched when Hull Suite is active AND the
  // relevant filter is enabled, so we don't waste calls otherwise.
  const hullActive = activeIndicators.has('hull');
  const trendHtfSec = parseInt(hullSettings.trendHtf, 10) * 60;
  const hullHtfSec = parseInt(hullSettings.hullHtf, 10) * 60;
  const htfTrendEnabled = hullActive && hullSettings.useHTFTrend && trendHtfSec > 0;
  const htfHullEnabled = hullActive && hullSettings.useHtfHull && hullHtfSec > 0;
  // Pull ~400 HTF candles (enough for EMA(200) + buffer)
  const htfRange = (n: number, sec: number) => n * sec * 1000;

  const { data: htfTrendData } = useQuery({
    queryKey: ['candles-htf', marketId, trendHtfSec, htfTrendEnabled],
    queryFn: () => getCandles(marketId, trendHtfSec, now - htfRange(400, trendHtfSec), now),
    refetchInterval: 60000,
    enabled: htfTrendEnabled,
  });
  const { data: htfHullData } = useQuery({
    queryKey: ['candles-htf', marketId, hullHtfSec, htfHullEnabled],
    queryFn: () => getCandles(marketId, hullHtfSec, now - htfRange(400, hullHtfSec), now),
    refetchInterval: 60000,
    enabled: htfHullEnabled,
  });

  // Map raw Perpl candle payloads -> chart Candle / volume points.
  const mapCandles = useCallback((raw: any[]): Candle[] =>
    raw.map((c) => ({ time: Math.floor(c.t / 1000), open: c.o / pd, high: c.h / pd, low: c.l / pd, close: c.c / pd })), [pd]);
  const mapVolume = useCallback((raw: any[]) =>
    raw.map((c: any) => ({
      time: Math.floor(c.t / 1000) as any,
      value: parseInt(c.v || '0') / 1e6,
      color: c.c >= c.o ? 'rgba(63,185,80,0.25)' : 'rgba(248,81,73,0.25)',
    })), []);

  // ----- scroll-back pagination: fetch the next older chunk and PREPEND it -----
  const loadOlder = useCallback(async () => {
    if (loadingOlderRef.current || !hasMoreHistoryRef.current) return;
    const oldestMs = oldestTimeRef.current;
    if (!oldestMs || !candleSeriesRef.current) return;
    let span = MAX_BARS * resolution * 1000;        // never exceed Perpl's single-request cap
    let fromMs = oldestMs - span;
    const toMs = oldestMs;
    if (lastOlderFromRef.current === fromMs) return; // same range already attempted
    lastOlderFromRef.current = fromMs;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    try {
      let resp: any;
      try {
        resp = await getCandles(marketId, resolution, fromMs, toMs);
      } catch {
        // Perpl 500 (range too large) -> retry once with a smaller range, then give up gracefully.
        span = Math.floor(span / 2);
        fromMs = oldestMs - span;
        try { resp = await getCandles(marketId, resolution, fromMs, toMs); }
        catch { return; }
      }
      const older = (resp?.d || []).filter((c: any) => c.t < oldestMs);
      if (!older.length) { hasMoreHistoryRef.current = false; return; }
      // merge + de-dupe by candle time + sort ascending
      const byTime = new Map<number, any>();
      for (const c of older) byTime.set(c.t, c);
      for (const c of rawCandlesRef.current) byTime.set(c.t, c);
      const merged = [...byTime.values()].sort((a, b) => a.t - b.t);
      const added = merged.length - rawCandlesRef.current.length;
      if (added <= 0) { hasMoreHistoryRef.current = false; return; }
      rawCandlesRef.current = merged;
      candlesRef.current = mapCandles(merged);
      oldestTimeRef.current = merged[0].t;
      // preserve the visible range: prepending `added` bars shifts every logical index right by `added`
      const ts = chartRef.current?.timeScale();
      const vr = ts?.getVisibleLogicalRange();
      candleSeriesRef.current.setData(candlesRef.current as any);
      volumeSeriesRef.current?.setData(mapVolume(merged) as any);
      if (ts && vr) ts.setVisibleLogicalRange({ from: vr.from + added, to: vr.to + added });
      setCandlesEpoch((e) => e + 1); // refresh indicators over the now-longer history
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, [marketId, resolution, mapCandles, mapVolume]);
  useEffect(() => { loadOlderRef.current = loadOlder; }, [loadOlder]);

  // =========== CHART CREATION ===========
  useEffect(() => {
    if (!mainChartRef.current || !data?.d?.length) return;

    const container = mainChartRef.current;
    container.innerHTML = '';
    indicatorSeriesRef.current.clear();
    liveCandleRef.current = null;

    const cc = chartPalette();
    const mobileView = window.innerWidth < 768;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: mobileView ? 300 : mainChartHeight,
      layout: { background: { type: ColorType.Solid, color: cc.bg }, textColor: cc.text, fontSize: 11 },
      // Very faint grid on the main chart so it doesn't cut across the Hull band
      // (grid lines render above back-layer primitives, so opacity there can't
      // hide them — keeping the grid itself subtle is the reliable fix).
      grid: {
        vertLines: { color: cc.dsb ? cc.grid : 'rgba(128,128,128,0.05)' },
        horzLines: { color: cc.dsb ? cc.grid : 'rgba(128,128,128,0.05)' },
      },
      crosshair: cc.dsb
        ? { mode: 0, vertLine: { color: cc.border, labelBackgroundColor: cc.accent }, horzLine: { color: cc.border, labelBackgroundColor: cc.accent } }
        : { mode: 0 },
      rightPriceScale: { borderColor: cc.border },
      timeScale: { borderColor: cc.border, timeVisible: true, secondsVisible: resolution <= 300 },
    });
    chartRef.current = chart;

    const minMove = 1 / pd;
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: cc.up, downColor: cc.down,
      borderDownColor: cc.down, borderUpColor: cc.up,
      wickDownColor: cc.down, wickUpColor: cc.up,
      priceFormat: { type: 'price', precision: priceDec, minMove },
      ...(cc.dsb ? { priceLineColor: cc.accent, priceLineStyle: 2 } : {}),
    });
    candleSeriesRef.current = candleSeries;
    // v5 markers are a plugin (series.setMarkers was removed). One instance for
    // the Hull BUY/SELL arrows, refreshed by the Hull overlay effect.
    hullMarkersRef.current = createSeriesMarkers(candleSeries, []);

    // Seed the merged candle store from the initial fetch + reset pagination state
    // (this effect only runs on market/resolution change -> intentional fresh load).
    rawCandlesRef.current = data.d.slice();
    oldestTimeRef.current = data.d[0]?.t ?? 0;
    hasMoreHistoryRef.current = true;
    loadingOlderRef.current = false;
    lastOlderFromRef.current = 0;
    followLiveRef.current = true;
    const candles: Candle[] = mapCandles(data.d);
    candlesRef.current = candles;
    candleSeries.setData(candles as any);
    // QQEX warmup anchor: signals never exist before this time, no matter how much
    // older history pagination later prepends (keeps the signal set session-stable).
    qqexAnchorTimeRef.current = candles[Math.min(QQEX_WARMUP_BARS, Math.max(0, candles.length - 1))]?.time ?? 0;

    // Initialize live candle tracking from last data candle
    const last = candles[candles.length - 1];
    if (last) {
      liveCandleRef.current = { ...last };
    }

    // Volume
    const volS = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'volume' });
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    volS.setData(mapVolume(rawCandlesRef.current) as any);
    volumeSeriesRef.current = volS;

    // Entry/Liq lines are managed in a separate effect (so opening/closing a position
    // updates them WITHOUT rebuilding the chart and wiping paginated history).

    // Stored H-Lines
    hlines.forEach((h) => {
      candleSeries.createPriceLine({ price: h.price, color: '#a78bfa80', lineWidth: 1, lineStyle: 0, axisLabelVisible: true, title: '' });
    });

    // Patterns
    const _ric = typeof requestIdleCallback === 'function' ? requestIdleCallback : (cb: () => void) => setTimeout(cb, 50);
    _ric(() => {
      const detected = detectAllPatterns(candles.map((c) => ({ ...c })));
      setPatterns(detected);
      const markers = detected.filter((p) => p.direction !== 'neutral').map((p) => ({
        time: p.time as any,
        position: p.direction === 'bullish' ? 'belowBar' as const : 'aboveBar' as const,
        color: p.direction === 'bullish' ? '#3fb950' : '#f85149',
        shape: p.direction === 'bullish' ? 'arrowUp' as const : 'arrowDown' as const,
        text: p.label,
      }));
      if (markers.length) { markers.sort((a, b) => (a.time as number) - (b.time as number)); try { candleSeries.setMarkers(markers); } catch {} }
    });

    // Show the most recent bars at a readable zoom (Perpl-like); all fetched history
    // (~1000 bars) stays scrollable to the left. Avoids squishing 1000 bars to fit.
    if (candles.length > DEFAULT_VISIBLE_BARS) {
      chart.timeScale().setVisibleLogicalRange({ from: candles.length - DEFAULT_VISIBLE_BARS, to: candles.length + 1 });
    } else {
      chart.timeScale().fitContent();
    }

    // Scroll-back pagination: when the visible left edge nears the oldest loaded bar,
    // fetch the next older chunk. Also track whether the user is pinned to the live edge.
    chart.timeScale().subscribeVisibleLogicalRangeChange((range: any) => {
      if (!range) return;
      const n = candlesRef.current.length;
      followLiveRef.current = range.to >= n - 2;
      if (range.from <= 50) loadOlderRef.current();
    });

    // Indicators key off candlesEpoch; bump it so they compute over this fresh data.
    setCandlesEpoch((e) => e + 1);

    const handleResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      indicatorSeriesRef.current.clear();
      // The chart owned the series the band was attached to; drop the stale ref
      // so the next chart re-attaches a fresh primitive.
      hullBandAttachedTo.current = null;
      hullMarkersRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, priceDec, pd, resolution, setPatterns, marketId, mapCandles, mapVolume]);

  // =========== ENTRY / LIQ PRICE LINES (separate — no chart rebuild) ===========
  useEffect(() => {
    const cs = candleSeriesRef.current;
    if (!cs) return;
    priceLinesRef.current.forEach((pl) => { try { cs.removePriceLine(pl); } catch {} });
    priceLinesRef.current = [];
    if (entryPrice) priceLinesRef.current.push(cs.createPriceLine({ price: entryPrice, color: chartPalette().entry, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'Entry' }));
    if (liqPrice) priceLinesRef.current.push(cs.createPriceLine({ price: liqPrice, color: chartPalette().liq, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'Liq' }));
  }, [entryPrice, liqPrice, candlesEpoch]);

  // =========== RESIZE (in-place, no chart rebuild) ===========
  useEffect(() => {
    if (chartRef.current && mainChartHeight > 50) {
      const mobileView = window.innerWidth < 768;
      chartRef.current.applyOptions({ height: mobileView ? 300 : mainChartHeight });
    }
  }, [mainChartHeight]);

  // =========== THEME UPDATE (in-place, no chart rebuild) ===========
  useEffect(() => {
    const cc = chartPalette();
    if (chartRef.current) {
      chartRef.current.applyOptions({
        layout: { background: { type: ColorType.Solid, color: cc.bg }, textColor: cc.text },
        grid: { vertLines: { color: cc.grid }, horzLines: { color: cc.grid } },
        rightPriceScale: { borderColor: cc.border },
        timeScale: { borderColor: cc.border },
      });
    }
    if (rsiChartObjRef.current) {
      rsiChartObjRef.current.applyOptions({
        layout: { background: { type: ColorType.Solid, color: cc.bg }, textColor: cc.text },
        grid: { vertLines: { color: cc.grid }, horzLines: { color: cc.grid } },
        rightPriceScale: { borderColor: cc.border },
      });
    }
    if (macdChartObjRef.current) {
      macdChartObjRef.current.applyOptions({
        layout: { background: { type: ColorType.Solid, color: cc.bg }, textColor: cc.text },
        grid: { vertLines: { color: cc.grid }, horzLines: { color: cc.grid } },
        rightPriceScale: { borderColor: cc.border },
      });
    }
  }, [theme]);

  // =========== INDICATOR OVERLAYS (separate from chart creation) ===========
  useEffect(() => {
    const chart = chartRef.current;
    const candles = candlesRef.current;
    if (!chart || !candles.length) return;

    const minMove = 1 / pd;
    const existing = indicatorSeriesRef.current;

    // Refresh-or-create: when active, recompute over the (possibly extended) candles and
    // setData on the existing series; when inactive, remove. This makes indicators follow
    // pagination + new bars without rebuilding the chart.
    const upsert = (key: string, active: boolean, compute: () => any[], color: string, style?: number) => {
      if (!active) {
        if (existing.has(key)) { try { chart.removeSeries(existing.get(key)!); } catch {} existing.delete(key); }
        return;
      }
      const lineData = compute();
      if (existing.has(key)) {
        try { existing.get(key)!.setData(lineData); return; } catch {}
        existing.delete(key);
      }
      const s = chart.addSeries(LineSeries, {
        color, lineWidth: 1, lineStyle: style,
        priceFormat: { type: 'price', precision: priceDec, minMove },
        lastValueVisible: false, priceLineVisible: false,
      });
      s.setData(lineData);
      existing.set(key, s);
    };

    upsert('ema9', activeIndicators.has('ema9'), () => ema(candles, 9).filter((p) => p.value !== null).map((p) => ({ time: p.time as any, value: p.value! })), IND_COLORS.ema9);
    upsert('ema21', activeIndicators.has('ema21'), () => ema(candles, 21).filter((p) => p.value !== null).map((p) => ({ time: p.time as any, value: p.value! })), IND_COLORS.ema21);
    upsert('sma50', activeIndicators.has('sma50'), () => sma(candles, 50).filter((p) => p.value !== null).map((p) => ({ time: p.time as any, value: p.value! })), IND_COLORS.sma50);

    const hasBB = activeIndicators.has('bb');
    const bb = hasBB ? bollingerBands(candles, 20, 2).filter((p) => p.upper !== null) : [];
    upsert('bb_upper', hasBB, () => bb.map((p) => ({ time: p.time as any, value: p.upper! })), IND_COLORS.bb_upper, 2);
    upsert('bb_middle', hasBB, () => bb.map((p) => ({ time: p.time as any, value: p.middle! })), IND_COLORS.bb_middle);
    upsert('bb_lower', hasBB, () => bb.map((p) => ({ time: p.time as any, value: p.lower! })), IND_COLORS.bb_lower, 2);
  }, [activeIndicators, candlesEpoch, priceDec, pd]);

  // =========== HULL SUITE OVERLAY ===========
  useEffect(() => {
    const chart = chartRef.current;
    const cs = candleSeriesRef.current;
    const candles = candlesRef.current;
    if (!chart || !cs || !candles.length) return;

    const existing = indicatorSeriesRef.current;
    const STATIC_HULL_KEYS = ['hull_vwap', 'hull_resistance', 'hull_support', 'hull_htfema', 'hull_shull'];
    // Per-segment Hull lines are prefixed and cleared dynamically
    const removeLines = () => {
      STATIC_HULL_KEYS.forEach((k) => {
        if (existing.has(k)) { try { chart.removeSeries(existing.get(k)!); } catch {} existing.delete(k); }
      });
      [...existing.keys()].forEach((k) => {
        if (k.startsWith('hull_mhull_seg_') || k.startsWith('hull_shull_seg_')) {
          try { chart.removeSeries(existing.get(k)!); } catch {}
          existing.delete(k);
        }
      });
    };

    // Keep the Hull band primitive attached to the CURRENT candle series (it is
    // recreated on theme/market/resolution changes). Attaching lazily here.
    if (!hullBandRef.current) hullBandRef.current = new HullBandPrimitive();
    if (!hullBoxRef.current) hullBoxRef.current = new HullBoxPrimitive();
    if (!qqexRibbonRef.current) qqexRibbonRef.current = new QqexRibbonPrimitive();
    if (!qqexRibbonAltRef.current) qqexRibbonAltRef.current = new QqexRibbonPrimitive();
    if (hullBandAttachedTo.current !== cs) {
      try { hullBandAttachedTo.current?.detachPrimitive?.(hullBandRef.current); } catch {}
      try { hullBandAttachedTo.current?.detachPrimitive?.(hullBoxRef.current); } catch {}
      try { hullBandAttachedTo.current?.detachPrimitive?.(qqexRibbonRef.current); } catch {}
      try { hullBandAttachedTo.current?.detachPrimitive?.(qqexRibbonAltRef.current); } catch {}
      // Attach boxes FIRST so overlays render on top of them (all zOrder 'bottom'
      // → attachment order decides). Anchor ribbon under primary ribbon per Pine.
      try { cs.attachPrimitive(hullBoxRef.current); } catch {}
      try { cs.attachPrimitive(qqexRibbonAltRef.current); } catch {}
      try { cs.attachPrimitive(qqexRibbonRef.current); } catch {}
      try { cs.attachPrimitive(hullBandRef.current); } catch {}
      hullBandAttachedTo.current = cs;
    }

    const clearQqexRibbons = () => {
      qqexRibbonRef.current?.setData([]);
      qqexRibbonAltRef.current?.setData([]);
    };

    if (!activeIndicators.has('hull')) {
      // Remove hull line series + clear the band/boxes/ribbons. Do NOT touch
      // markers — those belong to pattern detection set during chart creation.
      removeLines();
      clearQqexRibbons();
      hullBandRef.current.setData([], { fillAlpha: 0.5, edgeWidth: 2, switchColor: true });
      hullBoxRef.current.setData([], priceDec);
      setQqexReadout(null);
      return;
    }

    // ── QQEX v6.0 variant: dual direction-colored EMA ribbons + Pine-style
    // Open/Close labels + XQ/XC/XZ event marks + state-machine trade boxes. ──
    if (hullSettings.strategyVariant === 'qqex_v6') {
      removeLines();
      hullBandRef.current.setData([], { fillAlpha: 0.5, edgeWidth: 2, switchColor: true });

      // Forming-bar guard: all signals/events evaluate on CLOSED candles only —
      // the tick-built forming bar contributes nothing until it rolls.
      const closed = candles.length > 1 ? candles.slice(0, -1) : candles;
      const q = computeQqex(closed, hullSettings.qqex, hullSettings.maxBoxes, qqexAnchorTimeRef.current);

      // Primary ribbon green/red by direction; anchor ribbon aqua/blue by
      // altDirection, rendered beneath (attach order). Pine transp=90 → 0.10 fill.
      qqexRibbonRef.current!.setData(q.ribbon, { posRgb: '61,220,132', negRgb: '255,84,112', fillAlpha: 0.1, lineAlpha: 0.9 });
      qqexRibbonAltRef.current!.setData(q.ribbonAlt, { posRgb: '0,188,212', negRgb: '41,98,255', fillAlpha: 0.1, lineAlpha: 0.75 });

      setHullReadout(null);
      setQqexReadout(q.readout);

      if (hullSettings.showArrows || hullSettings.showBuySell) {
        // Pine label set — no price/PnL on markers (PnL lives on the trade box).
        const scc = chartPalette();
        const OPEN_STYLE = {
          open_long: { position: 'belowBar' as const, color: scc.sigLong, shape: 'arrowUp' as const, text: scc.dsb ? 'Open long' : 'Open LONG' },
          open_short: { position: 'aboveBar' as const, color: scc.sigShort, shape: 'arrowDown' as const, text: scc.dsb ? 'Open short' : 'Open SHORT' },
          close_long: { position: 'aboveBar' as const, color: scc.sigClose, shape: 'arrowDown' as const, text: scc.dsb ? 'Close long' : 'Close LONG' },
          close_short: { position: 'belowBar' as const, color: scc.sigClose, shape: 'arrowUp' as const, text: scc.dsb ? 'Close short' : 'Close SHORT' },
        };
        const markers: any[] = q.markers.map((m) => ({
          time: m.time as any,
          ...OPEN_STYLE[m.kind],
          text: hullSettings.showBuySell ? OPEN_STYLE[m.kind].text : '',
          size: 2,
        }));
        // Event marks (triangles in Pine; lightweight-charts has no triangle
        // shape → small labelled circles). XQ short uses gray, not Pine's black
        // (invisible on the dark theme).
        const TRI_STYLE: Record<string, { long: string; short: string }> = {
          xc: { long: '#808000', short: '#ef4444' },
          xq: { long: '#2962FF', short: '#94a3b8' },
          xz: { long: '#00BCD4', short: '#e879f9' },
        };
        for (const t of q.triangles) {
          markers.push({
            time: t.time as any,
            position: t.side === 'long' ? 'belowBar' as const : 'aboveBar' as const,
            color: TRI_STYLE[t.kind][t.side],
            shape: 'circle' as const,
            text: t.kind.toUpperCase(),
            size: 0.6,
          });
        }
        markers.sort((a, b) => (a.time as number) - (b.time as number));
        try { hullMarkersRef.current?.setMarkers(markers); } catch {}
      } else {
        try { hullMarkersRef.current?.setMarkers([]); } catch {}
      }

      hullBoxRef.current.setData(hullSettings.showTradeBoxes ? q.trades : [], priceDec);
      return;
    }
    clearQqexRibbons();
    setQqexReadout(null);

    // Build base volumes from the merged raw candle store (covers paginated history too).
    const baseVolumes: number[] = rawCandlesRef.current.map((c: any) => parseInt(c.v || '0') / 1e6);

    // HTF Hull source
    const htfHullSrc = htfHullEnabled && htfHullData?.d?.length
      ? computeHTFHullSrc(
          htfHullData.d.map((c: any) => ({
            time: Math.floor(c.t / 1000), open: c.o / pd, high: c.h / pd, low: c.l / pd, close: c.c / pd,
          })),
          hullSettings.hullMode,
          Math.max(2, Math.floor(hullSettings.hullLength * hullSettings.hullLengthMult)),
        )
      : undefined;

    const htfTrendCandles: Candle[] | undefined = htfTrendEnabled && htfTrendData?.d?.length
      ? htfTrendData.d.map((c: any) => ({
          time: Math.floor(c.t / 1000), open: c.o / pd, high: c.h / pd, low: c.l / pd, close: c.c / pd,
        }))
      : undefined;

    const result = computeHullSuite({
      baseCandles: candles,
      baseVolumes,
      htfHullSrc,
      htfTrendCandles,
      settings: hullSettings,
    });

    const minMove = 1 / pd;

    // Clear any previously rendered Hull segment series (we re-render every time)
    [...existing.keys()].forEach((k) => {
      if (k.startsWith('hull_mhull_seg_') || k.startsWith('hull_shull_seg_')) {
        try { chart.removeSeries(existing.get(k)!); } catch {}
        existing.delete(k);
      }
    });

    // Band opacity from the Band Transparency setting. Kept fairly solid so the
    // background grid doesn't show through the ribbon (candles still render on
    // top — the band is behind them).
    const bandA = Math.max(0.5, Math.min(0.9, 1 - hullSettings.bandTransparency / 100));

    const ensureLine = (key: string, points: { time: number; value: number }[], opts: any) => {
      if (existing.has(key)) {
        try { chart.removeSeries(existing.get(key)!); } catch {}
        existing.delete(key);
      }
      if (!points.length) return;
      const s = chart.addSeries(LineSeries, opts);
      s.setData(points.map((p) => ({ time: p.time as any, value: p.value })));
      existing.set(key, s);
    };

    // Hull ribbon — a TRUE filled band between MHULL and SHULL (Pine `fill(Fi1, Fi2)`),
    // trend-coloured, drawn beneath the candles via a canvas primitive. When "Show as
    // band" is off, collapse to just the MHULL edge by passing equal top/bottom.
    const mhullByTime = new Map(result.mhull.map((p) => [p.time, p.value]));
    const shullByTime = new Map(result.shull.map((p) => [p.time, p.value]));
    const bandPoints: HullBandPoint[] = [];
    for (let i = 0; i < candles.length; i++) {
      const t = candles[i].time;
      const m = mhullByTime.get(t);
      const sh = shullByTime.get(t);
      if (m === undefined) continue;
      const bottom = hullSettings.showBand && sh !== undefined ? (sh as number) : (m as number);
      bandPoints.push({ time: t, mhull: m as number, shull: bottom, bull: result.hullBullAt[i] });
    }
    hullBandRef.current.setData(bandPoints, {
      fillAlpha: bandA,
      edgeWidth: Math.max(1.5, Math.min(4, hullSettings.hullThickness)),
      switchColor: hullSettings.switchColor,
    });

    // Shared lineOpts for the non-Hull plots (VWAP, S/R, HTF EMA)
    const lineOpts = {
      priceFormat: { type: 'price' as const, precision: priceDec, minMove },
      lastValueVisible: false,
      priceLineVisible: false,
    };

    // VWAP — blue, secondary to the Hull band (thinner/softer so the ribbon leads)
    if (hullSettings.useVWAP) {
      ensureLine('hull_vwap', result.vwap, { ...lineOpts, color: 'rgba(96,165,250,0.6)', lineWidth: 1 as any, lastValueVisible: true });
    } else if (existing.has('hull_vwap')) {
      try { chart.removeSeries(existing.get('hull_vwap')!); } catch {}
      existing.delete('hull_vwap');
    }

    // S/R — soft dotted LEVELS (deliberately faint + dotted so they don't read as extra
    // Hull lines next to the ribbon).
    if (hullSettings.useSR) {
      ensureLine('hull_resistance', result.resistance, { ...lineOpts, color: 'rgba(255,84,112,0.38)', lineWidth: 1 as any, lineStyle: 1, lastValueVisible: true });
      ensureLine('hull_support', result.support, { ...lineOpts, color: 'rgba(61,220,132,0.38)', lineWidth: 1 as any, lineStyle: 1, lastValueVisible: true });
    } else {
      ['hull_resistance', 'hull_support'].forEach((k) => {
        if (existing.has(k)) { try { chart.removeSeries(existing.get(k)!); } catch {} existing.delete(k); }
      });
    }

    // HTF EMA — orange, readable but secondary
    if (hullSettings.useHTFTrend && result.htfEma.length) {
      ensureLine('hull_htfema', result.htfEma, { ...lineOpts, color: 'rgba(249,115,22,0.7)', lineWidth: 2 as any, lastValueVisible: true });
    } else if (existing.has('hull_htfema')) {
      try { chart.removeSeries(existing.get('hull_htfema')!); } catch {}
      existing.delete('hull_htfema');
    }

    // Snapshot latest values for the readout strip
    const last = <T extends { value: number }>(arr: T[]) => arr.length ? arr[arr.length - 1].value : null;
    setHullReadout({
      mhull: last(result.mhull),
      shull: last(result.shull),
      vwap: hullSettings.useVWAP ? last(result.vwap) : null,
      resistance: hullSettings.useSR ? last(result.resistance) : null,
      support: hullSettings.useSR ? last(result.support) : null,
      htfEma: hullSettings.useHTFTrend ? last(result.htfEma) : null,
      lastBull: result.hullBullAt.length ? result.hullBullAt[result.hullBullAt.length - 1] : true,
      signals: (hullSettings.strategyVariant === 'crossover' ? result.crossoverSignals : result.signals).length,
    });

    // Which signal set drives arrows + boxes depends on the chosen strategy variant.
    const selectedSignals = hullSettings.strategyVariant === 'crossover'
      ? result.crossoverSignals
      : result.signals;

    // Build the actual TRADES (entry signal → exit at the next opposite signal),
    // capped to the last N so the chart stays readable. Everything drawn —
    // entry arrows, exit markers, and the connecting box — comes from these, so
    // the chart clearly answers "enter here / exit here".
    const trades = (hullSettings.showArrows || hullSettings.showBuySell || hullSettings.showTradeBoxes)
      ? buildTradeBoxes({
          candles,
          signals: selectedSignals,
          mode: hullSettings.boxMode,
          atrLen: hullSettings.atrLen,
          atrTargetMult: hullSettings.atrTargetMult,
          atrStopMult: hullSettings.atrStopMult,
          maxBoxes: hullSettings.maxBoxes,
        })
      : [];

    const fmtP = (p: number) => p.toLocaleString(undefined, { minimumFractionDigits: priceDec, maximumFractionDigits: priceDec });

    // Entry + exit markers with prices and P&L.
    if ((hullSettings.showArrows || hullSettings.showBuySell) && trades.length) {
      const markers: any[] = [];
      for (const t of trades) {
        const long = t.side === 'long';
        // Entry: arrow on the signal candle, tagged with direction + price.
        markers.push({
          time: t.entryTime as any,
          position: long ? 'belowBar' : 'aboveBar',
          color: long ? '#22c55e' : '#ef4444',
          shape: long ? 'arrowUp' : 'arrowDown',
          text: hullSettings.showBuySell ? `${long ? 'LONG' : 'SHORT'} ${fmtP(t.entryPrice)}` : '',
        });
        // Exit: only for a closed trade (opposite signal / target / stop hit).
        if (t.outcome !== 'open') {
          const win = t.pnlPct !== undefined ? t.pnlPct >= 0 : t.outcome !== 'stop';
          const pnl = t.pnlPct !== undefined ? ` ${win ? '+' : ''}${t.pnlPct.toFixed(2)}%` : '';
          markers.push({
            time: t.exitTime as any,
            position: long ? 'aboveBar' : 'belowBar',
            color: win ? '#16a34a' : '#dc2626',
            shape: 'circle',
            text: hullSettings.showBuySell ? `EXIT ${fmtP(t.exitPrice)}${pnl}` : '',
          });
        }
      }
      markers.sort((a, b) => (a.time as number) - (b.time as number));
      try { hullMarkersRef.current?.setMarkers(markers); } catch {}
    } else {
      try { hullMarkersRef.current?.setMarkers([]); } catch {}
    }

    // Connecting trade boxes (subtle span; latest = active trade, bolder).
    hullBoxRef.current.setData(hullSettings.showTradeBoxes ? trades : [], priceDec);
  }, [activeIndicators, hullSettings, candlesEpoch, htfTrendData, htfHullData, htfTrendEnabled, htfHullEnabled, pd, priceDec]);

  // =========== H-LINE CLICK HANDLER ===========
  useEffect(() => {
    const chart = chartRef.current;
    const cs = candleSeriesRef.current;
    if (!chart || !cs) return;

    const handleClick = (param: any) => {
      if (!isDrawingHLine || !param.point) return;
      const price = cs.coordinateToPrice(param.point.y);
      if (price === null || price === undefined) return;
      const newH: HLineDrawing = { id: Date.now().toString(), price };
      setHlines((prev) => [...prev, newH]);
      cs.createPriceLine({ price, color: '#a78bfa80', lineWidth: 1, lineStyle: 0, axisLabelVisible: true, title: '' });
      setIsDrawingHLine(false);
    };

    chart.subscribeClick(handleClick);
    return () => { chart.unsubscribeClick(handleClick); };
  }, [isDrawingHLine]);

  // =========== LIVE CANDLE STREAM (Perpl WS candles@<mkt>*<res>) ===========
  // Real trade OHLCV pushed at block cadence via useMarketDataWs → candleBus —
  // this is what makes the chart update instantly like the Perpl app. Replaces
  // the mark-price approximation for the forming bar (real volume included) and
  // lands bar-closes the moment they happen. The 30s REST recent-sync stays as
  // reconciliation; the 3s mark tick below only fills in if this stream stalls.
  useEffect(() => {
    const off = candleBus.on((m) => {
      if (m.marketId !== marketId || m.resolution !== resolution) return;
      const cs = candleSeriesRef.current;
      if (!cs || !rawCandlesRef.current.length) return;
      for (const c of m.candles) {
        const arr = rawCandlesRef.current;
        const last = arr[arr.length - 1];
        // Older than the chart tail (e.g. snapshot backfill) — recent-sync owns it;
        // lightweight-charts update() only accepts the last bar or a newer one.
        if (!last || c.t < last.t) continue;
        const raw = { t: c.t, o: c.o, h: c.h, l: c.l, c: c.c, v: c.v };
        const mapped = { time: Math.floor(c.t / 1000), open: c.o / pd, high: c.h / pd, low: c.l / pd, close: c.c / pd };
        const isNew = c.t > last.t;
        if (isNew) {
          rawCandlesRef.current = [...arr, raw];
          candlesRef.current = [...candlesRef.current, mapped];
        } else {
          arr[arr.length - 1] = raw;
          candlesRef.current[candlesRef.current.length - 1] = mapped;
        }
        try { cs.update(mapped as any); } catch {}
        try {
          volumeSeriesRef.current?.update({
            time: mapped.time as any,
            value: parseInt(c.v || '0') / 1e6,
            color: c.c >= c.o ? 'rgba(63,185,80,0.25)' : 'rgba(248,81,73,0.25)',
          });
        } catch {}
        liveCandleRef.current = { ...mapped };
        lastWsCandleAtRef.current = Date.now();
        if (isNew) setCandlesEpoch((e) => e + 1); // a bar completed → indicators refresh
      }
    });
    return off;
  }, [marketId, resolution, pd]);

  // =========== REAL-TIME CANDLE UPDATE (mark-price FALLBACK) ===========
  // Updates every time market state changes (every ~3s from /ws/feed).
  // Only acts when the candles@ WS stream has been silent >10s — the pushed
  // stream carries real trade OHLCV and always wins while alive.
  useEffect(() => {
    const market = markets[marketId];
    if (!market || !candleSeriesRef.current || !rawCandlesRef.current.length) return;
    if (Date.now() - lastWsCandleAtRef.current < 10000) return;

    const price = market.mark_price;
    if (!price || price <= 0) return;

    const nowSec = Math.floor(Date.now() / 1000);
    // Align to current candle period
    const candleTime = Math.floor(nowSec / resolution) * resolution;
    const live = liveCandleRef.current;

    if (!live || live.time < candleTime) {
      // New candle period started — create a new candle (lightweight-charts auto-follows
      // only if the view is at the right edge; if the user scrolled back it stays put).
      const newCandle = { time: candleTime, open: price, high: price, low: price, close: price };
      liveCandleRef.current = newCandle;
      candleSeriesRef.current.update({ ...newCandle, time: newCandle.time as any });
      // Keep the merged store in sync so pagination/indicators include the new bar.
      rawCandlesRef.current = [...rawCandlesRef.current, { t: candleTime * 1000, o: price * pd, h: price * pd, l: price * pd, c: price * pd, v: '0' }];
      candlesRef.current = [...candlesRef.current, newCandle];
      setCandlesEpoch((e) => e + 1);
    } else {
      // Update the forming candle in place (no epoch bump — avoids recompute every ~3s)
      live.high = Math.max(live.high, price);
      live.low = Math.min(live.low, price);
      live.close = price;
      candleSeriesRef.current.update({
        time: live.time as any,
        open: live.open,
        high: live.high,
        low: live.low,
        close: live.close,
      });
      const lastC = candlesRef.current[candlesRef.current.length - 1];
      if (lastC && lastC.time === live.time) { lastC.high = live.high; lastC.low = live.low; lastC.close = live.close; }
    }
  }, [markets, marketId, pd, resolution]);

  // =========== RECENT SYNC (incremental — corrects the live tail with official OHLC) ===========
  // Every 30s fetch only the last few candles and reconcile via series.update() (NOT a full
  // setData), so the view never jumps and paginated history is preserved. Appends a new bar
  // when a fresh interval has started.
  useEffect(() => {
    const id = setInterval(async () => {
      if (!candleSeriesRef.current || !rawCandlesRef.current.length) return;
      const nowMs = Date.now();
      const fromMs = nowMs - 6 * resolution * 1000;
      let resp: any;
      try { resp = await getCandles(marketId, resolution, fromMs, nowMs); } catch { return; }
      const recent: any[] = resp?.d || [];
      if (!recent.length) return;
      const lastT = rawCandlesRef.current[rawCandlesRef.current.length - 1].t;
      const byTime = new Map<number, any>(rawCandlesRef.current.map((c: any) => [c.t, c]));
      let appended = false;
      for (const c of recent) {
        if (!byTime.has(c.t) && c.t > lastT) appended = true;
        byTime.set(c.t, c); // update existing or add new
      }
      rawCandlesRef.current = [...byTime.values()].sort((a, b) => a.t - b.t);
      candlesRef.current = mapCandles(rawCandlesRef.current);
      // Patch the visual series tail in place (update() = no view jump).
      for (const c of recent) {
        candleSeriesRef.current.update({ time: Math.floor(c.t / 1000) as any, open: c.o / pd, high: c.h / pd, low: c.l / pd, close: c.c / pd });
        volumeSeriesRef.current?.update({ time: Math.floor(c.t / 1000) as any, value: parseInt(c.v || '0') / 1e6, color: c.c >= c.o ? 'rgba(63,185,80,0.25)' : 'rgba(248,81,73,0.25)' });
      }
      if (appended) setCandlesEpoch((e) => e + 1); // a bar completed -> refresh indicators
    }, 30000);
    return () => clearInterval(id);
  }, [marketId, resolution, pd, mapCandles]);

  // =========== RSI PANE ===========
  useEffect(() => {
    if (!rsiChartRef.current) return;
    if (!candlesRef.current.length || !activeIndicators.has('rsi')) {
      rsiChartRef.current.innerHTML = '';
      if (rsiChartObjRef.current) { rsiChartObjRef.current.remove(); rsiChartObjRef.current = null; }
      return;
    }
    const container = rsiChartRef.current;
    container.innerHTML = '';
    const candles: Candle[] = candlesRef.current;

    const rcc = chartPalette();
    const chart = createChart(container, {
      width: container.clientWidth, height: 100,
      layout: { background: { type: ColorType.Solid, color: rcc.bg }, textColor: rcc.text, fontSize: 10 },
      grid: { vertLines: { color: rcc.grid }, horzLines: { color: rcc.grid } },
      rightPriceScale: { borderColor: rcc.border }, timeScale: { visible: false }, crosshair: { mode: 0 },
    });
    rsiChartObjRef.current = chart;

    const rsiData = rsi(candles, 14);
    const s = chart.addSeries(LineSeries, { color: rcc.dsb ? rcc.accent : '#a78bfa', lineWidth: 1.5, priceFormat: { type: 'custom', formatter: (v: number) => v.toFixed(0) } });
    s.setData(rsiData.filter((p) => p.value !== null).map((p) => ({ time: p.time as any, value: p.value! })));
    s.createPriceLine({ price: 70, color: rcc.dsb ? rcc.down : '#f8514940', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' });
    s.createPriceLine({ price: 30, color: rcc.dsb ? rcc.up : '#3fb95040', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' });

    if (chartRef.current) {
      chartRef.current.timeScale().subscribeVisibleLogicalRangeChange((range: any) => { if (range) chart.timeScale().setVisibleLogicalRange(range); });
    }
    const handleResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener('resize', handleResize);
    return () => { window.removeEventListener('resize', handleResize); chart.remove(); rsiChartObjRef.current = null; };
  }, [candlesEpoch, priceDec, pd, activeIndicators.has('rsi'), resolution]);

  // =========== MACD PANE ===========
  useEffect(() => {
    if (!macdChartRef.current) return;
    if (!candlesRef.current.length || !activeIndicators.has('macd')) {
      macdChartRef.current.innerHTML = '';
      if (macdChartObjRef.current) { macdChartObjRef.current.remove(); macdChartObjRef.current = null; }
      return;
    }
    const container = macdChartRef.current;
    container.innerHTML = '';
    const candles: Candle[] = candlesRef.current;

    const mcc = chartPalette();
    const chart = createChart(container, {
      width: container.clientWidth, height: 100,
      layout: { background: { type: ColorType.Solid, color: mcc.bg }, textColor: mcc.text, fontSize: 10 },
      grid: { vertLines: { color: mcc.grid }, horzLines: { color: mcc.grid } },
      rightPriceScale: { borderColor: mcc.border }, timeScale: { visible: false }, crosshair: { mode: 0 },
    });
    macdChartObjRef.current = chart;

    const macdData = macd(candles, 12, 26, 9);
    const valid = macdData.filter((p) => p.macd !== null);

    const histS = chart.addSeries(HistogramSeries, { priceFormat: { type: 'custom', formatter: (v: number) => v.toFixed(4) } });
    histS.setData(valid.map((p) => ({ time: p.time as any, value: p.histogram ?? 0, color: (p.histogram ?? 0) >= 0 ? 'rgba(63,185,80,0.5)' : 'rgba(248,81,73,0.5)' })));

    const macdS = chart.addSeries(LineSeries, { color: mcc.dsb ? mcc.accent : '#a78bfa', lineWidth: 1.5, priceFormat: { type: 'custom', formatter: (v: number) => v.toFixed(4) }, lastValueVisible: false, priceLineVisible: false });
    macdS.setData(valid.filter((p) => p.macd !== null).map((p) => ({ time: p.time as any, value: p.macd! })));

    const sigS = chart.addSeries(LineSeries, { color: '#f59e0b', lineWidth: 1, priceFormat: { type: 'custom', formatter: (v: number) => v.toFixed(4) }, lastValueVisible: false, priceLineVisible: false });
    sigS.setData(valid.filter((p) => p.signal !== null).map((p) => ({ time: p.time as any, value: p.signal! })));

    if (chartRef.current) {
      chartRef.current.timeScale().subscribeVisibleLogicalRangeChange((range: any) => { if (range) chart.timeScale().setVisibleLogicalRange(range); });
    }
    const handleResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener('resize', handleResize);
    return () => { window.removeEventListener('resize', handleResize); chart.remove(); macdChartObjRef.current = null; };
  }, [candlesEpoch, priceDec, pd, activeIndicators.has('macd'), resolution]);

  const clearHlines = () => { setHlines([]); localStorage.removeItem(`hlines-${marketId}`); };

  // Current price display
  const market = markets[marketId];
  const currentPrice = market?.mark_price ?? 0;
  const prevPrice = market?.prev_price ?? currentPrice;
  const priceUp = currentPrice >= prevPrice;

  return (
    <div className="flex flex-col h-full min-h-0 relative">
      {/* Toolbar — prototype-styled: tf segmented pill + uniform indicator chips */}
      <div className="rd-sans flex items-center gap-2 px-4 py-2.5 border-b flex-wrap shrink-0" style={{ borderColor: 'var(--border)' }}>
        {/* Symbol + live price */}
        <span className="rd-mono text-[13px] font-bold mr-0.5" style={{ color: 'var(--text)' }}>{symbol}</span>
        {currentPrice > 0 && (
          <span className="rd-mono text-[13px] font-bold mr-1 transition-colors" style={{ color: priceUp ? 'var(--green)' : 'var(--red)' }}>
            ${currentPrice.toFixed(priceDec)}
          </span>
        )}

        {/* Timeframe segmented pill */}
        <div className="flex items-center gap-0.5 p-[3px] rounded-[9px]" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
          {TIMEFRAMES.map((t) => {
            const on = resolution === t.resolution;
            return (
              <button key={t.resolution} onClick={() => setResolution(t.resolution)}
                className="rd-mono px-[9px] py-[5px] rounded-[6px] text-[12px] font-semibold transition-colors"
                style={on ? { background: 'var(--accent)', color: '#fff' } : { color: 'var(--dim)' }}>
                {t.label}
              </button>
            );
          })}
        </div>

        <div className="w-px h-5 hidden sm:block" style={{ background: 'var(--border)' }} />

        {/* Indicator chips — uniform (active = accent-soft) */}
        {([
          ['ema9', 'EMA 9'], ['ema21', 'EMA 21'], ['sma50', 'SMA 50'],
          ['bb', 'BB'], ['rsi', 'RSI'], ['macd', 'MACD'],
        ] as [IndicatorKey, string][]).map(([key, label]) => {
          const on = activeIndicators.has(key);
          return (
            <button key={key} onClick={() => toggleIndicator(key)}
              className="rd-mono px-[11px] py-[5px] rounded-[8px] text-[11.5px] font-semibold transition-colors"
              style={on
                ? { background: 'var(--accent-soft)', color: 'var(--accent-2)', border: '1px solid transparent' }
                : { background: 'var(--surface-2)', color: 'var(--dim)', border: '1px solid var(--border)' }}>
              {label}
            </button>
          );
        })}

        {/* Hull Suite — dropdown with two strategy variants */}
        <div className="relative" onClick={(e) => e.stopPropagation()}>
          <button onClick={() => setHullMenuOpen((v) => !v)}
            className="rd-mono px-[11px] py-[5px] rounded-[8px] text-[11.5px] font-semibold transition-colors flex items-center gap-1"
            style={hullActive
              ? { background: 'var(--accent-soft)', color: 'var(--accent-2)', border: '1px solid transparent' }
              : { background: 'var(--surface-2)', color: 'var(--dim)', border: '1px solid var(--border)' }}>
            {hullActive && hullSettings.strategyVariant === 'qqex_v6' ? 'QQEX v6.0' : 'Hull Suite'}
            <svg className="w-2.5 h-2.5 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M19 9l-7 7-7-7" /></svg>
          </button>
          {hullMenuOpen && (
            <div className="absolute left-0 top-full mt-1 z-50 w-[248px] rounded-[10px] p-1.5 shadow-xl"
              style={{ background: 'var(--surface)', border: '1px solid var(--border-strong)' }}>
              <div className="px-2 py-1 text-[9.5px] font-bold uppercase tracking-wider" style={{ color: 'var(--faint)' }}>Strategy</div>
              {([
                ['entry_suite', 'Hull Entry Suite', '7-filter, cooldown — fewer signals'],
                ['crossover', 'Hull Suite Strategy', 'Pure Hull crossover — flips on trend'],
                ['qqex_v6', 'QQEX v6.0 (QQE Cross)', 'RSIndex 60/40 entries, QQE-line exits, EMA ribbon gate'],
              ] as [HullSuiteSettings['strategyVariant'], string, string][]).map(([v, title, sub]) => {
                const sel = hullActive && hullSettings.strategyVariant === v;
                return (
                  <button key={v} onClick={() => pickHullVariant(v)}
                    className="w-full text-left px-2 py-1.5 rounded-[7px] transition-colors hover:bg-[var(--surface-2)]"
                    style={sel ? { background: 'var(--accent-soft)' } : {}}>
                    <div className="flex items-center justify-between">
                      <span className="text-[12px] font-semibold" style={{ color: sel ? 'var(--accent-2)' : 'var(--text)' }}>{title}</span>
                      {sel && <span className="text-[10px]" style={{ color: 'var(--accent-2)' }}>●</span>}
                    </div>
                    <div className="text-[10px] mt-0.5" style={{ color: 'var(--dim)' }}>{sub}</div>
                  </button>
                );
              })}
              <div className="h-px my-1.5" style={{ background: 'var(--border)' }} />
              <button onClick={() => setHull({ showTradeBoxes: !hullSettings.showTradeBoxes })}
                className="w-full flex items-center justify-between px-2 py-1.5 rounded-[7px] hover:bg-[var(--surface-2)]">
                <span className="text-[11.5px]" style={{ color: 'var(--text)' }}>Trade boxes</span>
                <span className="text-[10px] font-bold" style={{ color: hullSettings.showTradeBoxes ? 'var(--accent-2)' : 'var(--faint)' }}>{hullSettings.showTradeBoxes ? 'ON' : 'OFF'}</span>
              </button>
              <button onClick={() => setHull({ boxMode: hullSettings.boxMode === 'target' ? 'backtest' : 'target' })}
                className="w-full flex items-center justify-between px-2 py-1.5 rounded-[7px] hover:bg-[var(--surface-2)]">
                <span className="text-[11.5px]" style={{ color: 'var(--text)' }}>Box type</span>
                <span className="text-[10px] font-semibold" style={{ color: 'var(--accent-2)' }}>{hullSettings.boxMode === 'target' ? 'Target + Stop (per trade)' : 'Trade P&L'}</span>
              </button>
              {hullActive && (
                <button onClick={() => { toggleIndicator('hull'); setHullMenuOpen(false); }}
                  className="w-full text-left px-2 py-1.5 rounded-[7px] hover:bg-[var(--surface-2)] text-[11.5px]" style={{ color: 'var(--red)' }}>
                  Hide Hull Suite
                </button>
              )}
            </div>
          )}
        </div>

        {/* Hull Suite settings cog — subtle */}
        {activeIndicators.has('hull') && (
          <button onClick={() => setHullPanelOpen((v) => !v)} title="Hull Suite settings"
            className="w-[28px] h-[28px] grid place-items-center rounded-[8px] transition-colors"
            style={hullPanelOpen
              ? { background: 'var(--accent-soft)', color: 'var(--accent-2)', border: '1px solid transparent' }
              : { background: 'var(--surface-2)', color: 'var(--faint)', border: '1px solid var(--border)' }}>
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </button>
        )}

        <div className="w-px h-5 hidden md:block" style={{ background: 'var(--border)' }} />

        {/* H-Line drawing — desktop only, subtle */}
        <button onClick={() => setIsDrawingHLine(!isDrawingHLine)}
          className="hidden md:flex rd-mono px-[10px] py-[5px] rounded-[8px] text-[11px] font-semibold items-center gap-1 transition-colors"
          style={isDrawingHLine
            ? { background: 'var(--accent-soft)', color: 'var(--accent-2)', border: '1px solid transparent' }
            : { background: 'var(--surface-2)', color: 'var(--faint)', border: '1px solid var(--border)' }}>
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeWidth={2} d="M3 12h18" /></svg>
          H-Line
        </button>
        {hlines.length > 0 && (
          <button onClick={clearHlines} className="rd-mono px-2 py-1 rounded text-[11px] transition-colors hover:text-danger" style={{ color: 'var(--faint)' }}>Clear</button>
        )}
        {isDrawingHLine && (
          <span className="text-[11px] animate-pulse ml-1" style={{ color: 'var(--accent-2)' }}>
            Click on chart to place line
            <button onClick={() => setIsDrawingHLine(false)} className="ml-1" style={{ color: 'var(--faint)' }}>×</button>
          </span>
        )}
      </div>

      {/* QQEX v6 readout strip — RSIndex / QQE trailing line / trade state */}
      {activeIndicators.has('hull') && hullSettings.strategyVariant === 'qqex_v6' && qqexReadout && (
        <div className="shrink-0 flex items-center gap-3 px-3 py-1 border-b border-text-secondary/10 bg-bg-secondary/30 text-[10px] font-mono overflow-x-auto whitespace-nowrap">
          <span style={{ color: qqexReadout.trend === 1 ? '#22c55e' : '#ef4444' }}>
            RSIndex <b>{qqexReadout.rsindex?.toFixed(2) ?? '—'}</b>
          </span>
          <span style={{ color: qqexReadout.trend === 1 ? '#22c55e' : '#ef4444' }}>
            QQE TL <b>{qqexReadout.tl?.toFixed(2) ?? '—'}</b>
          </span>
          <span className="text-text-secondary">
            TREND <b style={{ color: qqexReadout.trend === 1 ? '#22c55e' : '#ef4444' }}>{qqexReadout.trend === 1 ? 'UP' : 'DOWN'}</b>
          </span>
          <span className="text-text-secondary">
            STATE <b style={{ color: qqexReadout.state === 'long' ? '#22c55e' : qqexReadout.state === 'short' ? '#ef4444' : undefined }}>{qqexReadout.state.toUpperCase()}</b>
          </span>
          <span className="text-text-secondary/50 ml-auto">signals: <b>{qqexReadout.openSignals}</b></span>
        </div>
      )}

      {/* Hull Suite readout strip — shows latest indicator values for comparison */}
      {activeIndicators.has('hull') && hullSettings.strategyVariant !== 'qqex_v6' && hullReadout && (
        <div className="shrink-0 flex items-center gap-3 px-3 py-1 border-b border-text-secondary/10 bg-bg-secondary/30 text-[10px] font-mono overflow-x-auto whitespace-nowrap">
          <span style={{ color: hullReadout.lastBull ? '#22c55e' : '#ef4444' }}>
            HULL <b>{hullReadout.mhull?.toFixed(priceDec) ?? '—'}</b>
          </span>
          <span style={{ color: hullReadout.lastBull ? '#22c55e' : '#ef4444' }}>
            SHULL <b>{hullReadout.shull?.toFixed(priceDec) ?? '—'}</b>
          </span>
          {hullSettings.useVWAP && (
            <span style={{ color: '#3b82f6' }}>VWAP <b>{hullReadout.vwap?.toFixed(priceDec) ?? '—'}</b></span>
          )}
          {hullSettings.useSR && (<>
            <span style={{ color: '#ef4444' }}>R <b>{hullReadout.resistance?.toFixed(priceDec) ?? '—'}</b></span>
            <span style={{ color: '#22c55e' }}>S <b>{hullReadout.support?.toFixed(priceDec) ?? '—'}</b></span>
          </>)}
          {hullSettings.useHTFTrend && (
            <span style={{ color: '#f97316' }}>HTF EMA <b>{hullReadout.htfEma?.toFixed(priceDec) ?? '—'}</b></span>
          )}
          <span className="text-text-secondary/50 ml-auto">signals: <b>{hullReadout.signals}</b></span>
        </div>
      )}

      {/* Main chart — flex-1 fills remaining space, shrinks when sub-panes active */}
      <div className="relative w-full flex-1 min-h-[200px]">
        <div ref={mainChartRef} className={clsx('w-full h-full', isDrawingHLine && 'cursor-crosshair')} />
        {loadingOlder && (
          <div className="absolute top-1/2 left-2 -translate-y-1/2 flex items-center gap-1.5 px-2 py-1 rounded-md bg-bg-card/80 border border-text-secondary/15 text-[10px] text-text-secondary pointer-events-none z-10">
            <span className="w-2.5 h-2.5 border-[1.5px] border-accent border-t-transparent rounded-full animate-spin" />
            loading history…
          </div>
        )}
      </div>

      {/* RSI — fixed height, always visible when active */}
      {activeIndicators.has('rsi') && (
        <div className="shrink-0 border-t border-text-secondary/10">
          <div className="px-3 py-0.5 text-[9px] text-text-secondary/50 font-semibold uppercase tracking-widest bg-bg-secondary/30">RSI (14)</div>
          <div ref={rsiChartRef} className="w-full" />
        </div>
      )}

      {/* MACD — fixed height, always visible when active */}
      {activeIndicators.has('macd') && (
        <div className="shrink-0 border-t border-text-secondary/10">
          <div className="px-3 py-0.5 text-[9px] text-text-secondary/50 font-semibold uppercase tracking-widest bg-bg-secondary/30">MACD (12, 26, 9)</div>
          <div ref={macdChartRef} className="w-full" />
        </div>
      )}

      <HullSuitePanel
        open={hullPanelOpen && activeIndicators.has('hull')}
        onClose={() => setHullPanelOpen(false)}
        settings={hullSettings}
        onChange={setHullSettings}
      />
    </div>
  );
}

import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';

// QQEX v6 ribbon: three EMAs (fast/med/slow) stroked 1/2/1 px + a fill between
// fast and slow, colored per bar by direction (two-color scheme, e.g. green/red
// for the primary ribbon, aqua/blue for the anchor ribbon). lightweight-charts
// line series cannot recolor per point, so the ribbon is drawn here as
// contiguous same-color runs (each run overlaps the next point so segments
// connect without gaps) — same approach as HullBandPrimitive.

export interface QqexRibbonPoint {
  time: number;   // unix seconds
  fast: number;
  med: number;
  slow: number;
  neg: boolean;   // direction < 0 → negColor; else posColor (Pine: 0 counts positive)
}

export interface QqexRibbonStyle {
  posRgb: string;   // "r,g,b"
  negRgb: string;
  fillAlpha: number;    // Pine transp=90 → 0.10
  lineAlpha: number;
}

export class QqexRibbonPrimitive {
  private _chart: IChartApi | null = null;
  private _series: ISeriesApi<any> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _points: QqexRibbonPoint[] = [];
  private _style: QqexRibbonStyle = { posRgb: '61,220,132', negRgb: '255,84,112', fillAlpha: 0.1, lineAlpha: 0.95 };
  private _paneView = new QqexRibbonPaneView(this);

  attached(param: { chart: IChartApi; series: ISeriesApi<any>; requestUpdate: () => void }) {
    this._chart = param.chart;
    this._series = param.series;
    this._requestUpdate = param.requestUpdate;
  }
  detached() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }
  updateAllViews() {}
  paneViews() { return [this._paneView]; }

  setData(points: QqexRibbonPoint[], style?: Partial<QqexRibbonStyle>) {
    this._points = points;
    if (style) this._style = { ...this._style, ...style };
    this._requestUpdate?.();
  }

  get chart() { return this._chart; }
  get series() { return this._series; }
  get points() { return this._points; }
  get style() { return this._style; }
}

class QqexRibbonPaneView {
  constructor(private _source: QqexRibbonPrimitive) {}
  zOrder() { return 'bottom' as const; }  // behind the candles
  renderer() { return new QqexRibbonRenderer(this._source); }
}

class QqexRibbonRenderer {
  constructor(private _source: QqexRibbonPrimitive) {}

  draw(target: any) {
    const src = this._source;
    const chart = src.chart;
    const series = src.series;
    if (!chart || !series) return;
    const pts = src.points;
    if (pts.length < 2) return;

    const ts = chart.timeScale();
    const coords: { x: number; yf: number; ym: number; ys: number; neg: boolean }[] = [];
    for (const p of pts) {
      const x = ts.timeToCoordinate(p.time as unknown as Time);
      const yf = series.priceToCoordinate(p.fast);
      const ym = series.priceToCoordinate(p.med);
      const ys = series.priceToCoordinate(p.slow);
      if (x === null || yf === null || ym === null || ys === null) continue;
      coords.push({ x, yf, ym, ys, neg: p.neg });
    }
    if (coords.length < 2) return;

    target.useBitmapCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const hr = scope.horizontalPixelRatio;
      const vr = scope.verticalPixelRatio;
      const st = src.style;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';

      let runStart = 0;
      for (let i = 1; i <= coords.length; i++) {
        const boundary = i === coords.length || coords[i].neg !== coords[runStart].neg;
        if (!boundary) continue;
        const runEnd = i === coords.length ? coords.length - 1 : i; // inclusive, overlap next run
        if (runEnd <= runStart) { runStart = i; continue; }
        const rgb = coords[runStart].neg ? st.negRgb : st.posRgb;

        // Fill between fast and slow
        ctx.beginPath();
        ctx.moveTo(coords[runStart].x * hr, coords[runStart].yf * vr);
        for (let j = runStart + 1; j <= runEnd; j++) ctx.lineTo(coords[j].x * hr, coords[j].yf * vr);
        for (let j = runEnd; j >= runStart; j--) ctx.lineTo(coords[j].x * hr, coords[j].ys * vr);
        ctx.closePath();
        ctx.fillStyle = `rgba(${rgb},${st.fillAlpha})`;
        ctx.fill();

        // Lines: fast 1px, med 2px, slow 1px (Pine linewidths)
        const stroke = (key: 'yf' | 'ym' | 'ys', width: number) => {
          ctx.strokeStyle = `rgba(${rgb},${st.lineAlpha})`;
          ctx.lineWidth = width * vr;
          ctx.beginPath();
          ctx.moveTo(coords[runStart].x * hr, coords[runStart][key] * vr);
          for (let j = runStart + 1; j <= runEnd; j++) ctx.lineTo(coords[j].x * hr, coords[j][key] * vr);
          ctx.stroke();
        };
        stroke('yf', 1);
        stroke('ym', 2);
        stroke('ys', 1);

        runStart = i;
      }
    });
  }
}

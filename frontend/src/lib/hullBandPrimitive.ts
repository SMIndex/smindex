import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';

// A lightweight-charts v5 series primitive that renders the Hull ribbon as a
// TRUE filled band between MHULL and SHULL (like the Pine `fill(Fi1, Fi2)`),
// colored by trend, with both edges stroked. Drawn beneath the candles so the
// price action reads on top of the band.

export interface HullBandPoint {
  time: number;   // unix seconds (chart time)
  mhull: number;
  shull: number;
  bull: boolean;  // MHULL > SHULL at this bar
}

const RGB_GREEN = '61,220,132';
const RGB_RED = '255,84,112';
const RGB_ORANGE = '249,115,22';

export class HullBandPrimitive {
  private _chart: IChartApi | null = null;
  private _series: ISeriesApi<any> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _points: HullBandPoint[] = [];
  private _fillAlpha = 0.5;
  private _edgeWidth = 2;
  private _switchColor = true;
  private _paneView = new HullBandPaneView(this);

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

  setData(points: HullBandPoint[], opts: { fillAlpha: number; edgeWidth: number; switchColor: boolean }) {
    this._points = points;
    this._fillAlpha = opts.fillAlpha;
    this._edgeWidth = opts.edgeWidth;
    this._switchColor = opts.switchColor;
    this._requestUpdate?.();
  }

  get chart() { return this._chart; }
  get series() { return this._series; }
  get points() { return this._points; }
  get fillAlpha() { return this._fillAlpha; }
  get edgeWidth() { return this._edgeWidth; }
  get switchColor() { return this._switchColor; }
}

class HullBandPaneView {
  constructor(private _source: HullBandPrimitive) {}
  zOrder() { return 'bottom' as const; }  // behind the candles
  renderer() { return new HullBandRenderer(this._source); }
}

class HullBandRenderer {
  constructor(private _source: HullBandPrimitive) {}

  draw(target: any) {
    const src = this._source;
    const chart = src.chart;
    const series = src.series;
    if (!chart || !series) return;
    const pts = src.points;
    if (pts.length < 2) return;

    const ts = chart.timeScale();
    // Map every point to media-space pixels once.
    const coords: { x: number; ym: number; ys: number; bull: boolean }[] = [];
    for (const p of pts) {
      const x = ts.timeToCoordinate(p.time as unknown as Time);
      const ym = series.priceToCoordinate(p.mhull);
      const ys = series.priceToCoordinate(p.shull);
      if (x === null || ym === null || ys === null) continue;
      coords.push({ x, ym, ys, bull: p.bull });
    }
    if (coords.length < 2) return;

    target.useBitmapCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const hr = scope.horizontalPixelRatio;
      const vr = scope.verticalPixelRatio;
      const rgb = (bull: boolean) => src.switchColor ? (bull ? RGB_GREEN : RGB_RED) : RGB_ORANGE;

      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';

      // Fill + stroke per contiguous same-trend run; each run overlaps the next
      // by one point so the colored segments connect without gaps.
      let runStart = 0;
      for (let i = 1; i <= coords.length; i++) {
        const isBoundary = i === coords.length || coords[i].bull !== coords[runStart].bull;
        if (!isBoundary) continue;
        const runEnd = i === coords.length ? coords.length - 1 : i; // inclusive, with overlap
        if (runEnd <= runStart) { runStart = i; continue; }
        const c = rgb(coords[runStart].bull);

        // Filled band polygon: forward along MHULL, back along SHULL.
        ctx.beginPath();
        ctx.moveTo(coords[runStart].x * hr, coords[runStart].ym * vr);
        for (let j = runStart + 1; j <= runEnd; j++) ctx.lineTo(coords[j].x * hr, coords[j].ym * vr);
        for (let j = runEnd; j >= runStart; j--) ctx.lineTo(coords[j].x * hr, coords[j].ys * vr);
        ctx.closePath();
        ctx.fillStyle = `rgba(${c},${src.fillAlpha})`;
        ctx.fill();

        // MHULL edge (dominant)
        ctx.strokeStyle = `rgba(${c},0.95)`;
        ctx.lineWidth = src.edgeWidth * vr;
        ctx.beginPath();
        ctx.moveTo(coords[runStart].x * hr, coords[runStart].ym * vr);
        for (let j = runStart + 1; j <= runEnd; j++) ctx.lineTo(coords[j].x * hr, coords[j].ym * vr);
        ctx.stroke();

        // SHULL edge (companion, thinner, same color)
        ctx.lineWidth = Math.max(1, src.edgeWidth * 0.6) * vr;
        ctx.beginPath();
        ctx.moveTo(coords[runStart].x * hr, coords[runStart].ys * vr);
        for (let j = runStart + 1; j <= runEnd; j++) ctx.lineTo(coords[j].x * hr, coords[j].ys * vr);
        ctx.stroke();

        runStart = i;
      }
    });
  }
}

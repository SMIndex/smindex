import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import type { TradeBox } from './hullSuite';

// Canvas primitive (lightweight-charts v5) that draws Hull strategy TRADE BOXES:
//  • backtest mode: a translucent green/red box from entry to exit + P&L% label.
//  • target mode:   a green target box (entry→target) and a red stop box
//    (entry→stop) spanning entry→exit, with price labels.
// Same rendering technique as HullBandPrimitive.

const GREEN = '8,153,129';   // #089981
const RED = '242,54,69';     // #f23645

export class HullBoxPrimitive {
  private _chart: IChartApi | null = null;
  private _series: ISeriesApi<any> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _boxes: TradeBox[] = [];
  private _priceDec = 1;
  private _paneView = new HullBoxPaneView(this);

  attached(param: { chart: IChartApi; series: ISeriesApi<any>; requestUpdate: () => void }) {
    this._chart = param.chart;
    this._series = param.series;
    this._requestUpdate = param.requestUpdate;
  }
  detached() { this._chart = null; this._series = null; this._requestUpdate = null; }
  updateAllViews() {}
  paneViews() { return [this._paneView]; }

  setData(boxes: TradeBox[], priceDec: number) {
    this._boxes = boxes;
    this._priceDec = priceDec;
    this._requestUpdate?.();
  }

  get chart() { return this._chart; }
  get series() { return this._series; }
  get boxes() { return this._boxes; }
  get priceDec() { return this._priceDec; }
}

class HullBoxPaneView {
  constructor(private _source: HullBoxPrimitive) {}
  zOrder() { return 'bottom' as const; } // BEHIND candles + band so it never obscures them
  renderer() { return new HullBoxRenderer(this._source); }
}

class HullBoxRenderer {
  constructor(private _source: HullBoxPrimitive) {}

  draw(target: any) {
    const src = this._source;
    const chart = src.chart;
    const series = src.series;
    if (!chart || !series || !src.boxes.length) return;
    const ts = chart.timeScale();
    const dec = src.priceDec;

    target.useBitmapCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const hr = scope.horizontalPixelRatio;
      const vr = scope.verticalPixelRatio;

      const X = (t: number) => {
        const x = ts.timeToCoordinate(t as unknown as Time);
        return x === null ? null : x * hr;
      };
      const Y = (p: number) => {
        const y = series.priceToCoordinate(p);
        return y === null ? null : y * vr;
      };

      const rect = (x0: number, x1: number, y0: number, y1: number, rgb: string, alpha: number) => {
        ctx.fillStyle = `rgba(${rgb},${alpha})`;
        ctx.fillRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
      };
      const label = (text: string, x: number, y: number, rgb: string) => {
        ctx.font = `${11 * vr}px monospace`;
        ctx.textBaseline = 'middle';
        const w = ctx.measureText(text).width;
        const padX = 4 * hr; const h = 14 * vr;
        ctx.fillStyle = 'rgba(15,17,22,0.85)';
        ctx.fillRect(x, y - h / 2, w + padX * 2, h);
        ctx.fillStyle = `rgba(${rgb},1)`;
        ctx.fillText(text, x + padX, y);
      };

      const n = src.boxes.length;
      src.boxes.forEach((b, idx) => {
        const latest = idx === n - 1;
        const x0 = X(b.entryTime);
        const x1 = X(b.exitTime);
        const yEntry = Y(b.entryPrice);
        if (x0 === null || x1 === null || yEntry === null) return;
        const xL = Math.min(x0, x1);
        const xR = Math.max(x0, x1);

        if (b.mode === 'target' && b.target !== undefined && b.stop !== undefined) {
          // PER-TRADE target/stop, bounded to THIS trade's time span [entry,exit]
          // so adjacent long/short trades never overlap. Green target zone
          // (entry→target) and red stop zone (entry→stop), TP/SL lines labelled.
          const yT = Y(b.target);
          const yS = Y(b.stop);
          if (yT !== null) {
            rect(xL, xR, yEntry, yT, GREEN, latest ? 0.16 : 0.10);
            ctx.strokeStyle = `rgba(${GREEN},${latest ? 0.95 : 0.7})`;
            ctx.lineWidth = (latest ? 1.6 : 1.1) * vr;
            ctx.beginPath(); ctx.moveTo(xL, yT); ctx.lineTo(xR, yT); ctx.stroke();
          }
          if (yS !== null) {
            rect(xL, xR, yEntry, yS, RED, latest ? 0.16 : 0.10);
            ctx.strokeStyle = `rgba(${RED},${latest ? 0.95 : 0.7})`;
            ctx.lineWidth = (latest ? 1.6 : 1.1) * vr;
            ctx.beginPath(); ctx.moveTo(xL, yS); ctx.lineTo(xR, yS); ctx.stroke();
          }
          // entry level guide across the trade
          ctx.strokeStyle = 'rgba(190,190,200,0.6)'; ctx.lineWidth = 1 * vr;
          ctx.setLineDash([3 * hr, 3 * hr]);
          ctx.beginPath(); ctx.moveTo(xL, yEntry); ctx.lineTo(xR, yEntry); ctx.stroke();
          ctx.setLineDash([]);
          // Labels at the right edge of each trade's span (spread out in time).
          if (yT !== null) label(`TP ${b.target.toFixed(dec)}`, xR + 2 * hr, yT, GREEN);
          if (yS !== null) label(`SL ${b.stop.toFixed(dec)}`, xR + 2 * hr, yS, RED);
        } else {
          // P&L mode: a solid entry→exit box coloured by win/loss (markers show
          // the LONG/SHORT + EXIT % labels).
          const yExit = Y(b.exitPrice);
          if (yExit !== null) {
            const rgb = b.win ? GREEN : RED;
            rect(x0, x1, yEntry, yExit, rgb, latest ? 0.18 : 0.11);
            ctx.strokeStyle = `rgba(${rgb},${latest ? 0.85 : 0.5})`;
            ctx.lineWidth = 1 * vr;
            ctx.strokeRect(xL, Math.min(yEntry, yExit), xR - xL, Math.abs(yEntry - yExit));
          }
        }
      });
    });
  }
}

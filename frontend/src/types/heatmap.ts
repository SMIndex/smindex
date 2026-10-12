export interface LiquidationBin {
  price: number;
  intensity: number;
  long_liq_usd: number;
  short_liq_usd: number;
}

export interface HeatmapData {
  market_id: number;
  current_price: number;
  bins: LiquidationBin[];
  timestamp: string;
}

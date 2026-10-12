export interface WhaleAlert {
  id: string;
  market_id: number;
  alert_type: 'large_order' | 'large_fill' | 'oi_divergence';
  side: string | null;
  size_usd: number;
  price: number | null;
  severity: 'medium' | 'high' | 'extreme';
  details: Record<string, any>;
  timestamp: string;
}

export interface OIDivergence {
  market_id: number;
  oi_change_pct: number;
  price_change_pct: number;
  direction: 'bullish_divergence' | 'bearish_divergence';
  timestamp: string;
}

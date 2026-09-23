export interface LeaderTrade {
  id: string;
  leader_id: string;
  market_id: number;
  side: 'buy' | 'sell';
  size: number;
  price: number;
  leverage: number;
  is_close: boolean;
  timestamp: string;
}

export interface CopyExecution {
  id: string;
  follower_id: string;
  leader_trade_id: string;
  leader_id: string;
  market_id: number;
  side: 'buy' | 'sell';
  intended_size: number;
  actual_size: number | null;
  intended_price: number;
  actual_price: number | null;
  leverage: number;
  status: 'submitted' | 'filled' | 'failed' | 'partial';
  error: string | null;
  submitted_at: string;
  filled_at: string | null;
  latency_ms: number | null;
}

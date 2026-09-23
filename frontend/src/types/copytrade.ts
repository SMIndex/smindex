export interface Leader {
  rank: number;
  wallet_address: string;
  username?: string | null;
  pnl_total: number;
  roi: number;
  volume: number;
  // Only present when fetched from DB (copy trade context)
  id?: string;
  display_name?: string | null;
  is_active?: boolean;
  stats?: LeaderStats;
}

export interface LeaderStats {
  total_trades: number;
  win_rate: number;
  pnl_total: number;
  roi: number;
  volume: number;
  pnl_7d: number;
  pnl_30d: number;
  sharpe_ratio: number;
  max_drawdown: number;
  avg_leverage: number;
  followers_count: number;
}

export interface FollowConfig {
  id: string;
  leader_id: string;
  allocation_usd: number;
  max_leverage: number;
  is_active: boolean;
  leader_wallet: string | null;
  leader_display_name: string | null;
}

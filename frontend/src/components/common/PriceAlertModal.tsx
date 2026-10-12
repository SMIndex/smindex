import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import Modal from '@/components/common/Modal';
import { useMarketStore } from '@/stores/marketStore';
import { usePriceAlertStore, type PriceAlert } from '@/stores/priceAlertStore';
import { useAuthStore } from '@/stores/authStore';
import { MARKETS, MARKET_IDS } from '@/config/constants';
import { formatPrice } from '@/lib/formatters';
import api from '@/lib/api';

interface PriceAlertModalProps {
  isOpen: boolean;
  onClose: () => void;
  defaultMarketId?: number;
}

interface ServerAlert {
  id: number;
  market_id: number;
  symbol: string;
  condition: string;
  target_price: number;
  status: string;
  created_at: string;
  triggered_at: string | null;
}

export default function PriceAlertModal({ isOpen, onClose, defaultMarketId }: PriceAlertModalProps) {
  const markets = useMarketStore((s) => s.markets);
  const { alerts, addAlert, removeAlert } = usePriceAlertStore();
  const token = useAuthStore((s) => s.token);
  const queryClient = useQueryClient();
  const [marketId, setMarketId] = useState(defaultMarketId ?? 1);
  const [direction, setDirection] = useState<'above' | 'below'>('above');
  const [price, setPrice] = useState(0);

  const config = MARKETS[marketId];
  const market = markets[marketId];

  // Fetch server-side alerts (for Telegram notifications)
  const { data: serverAlerts = [] } = useQuery({
    queryKey: ['server-price-alerts'],
    queryFn: () => api.get('/api/price-alerts').then((r) => r.data as ServerAlert[]),
    enabled: !!token && isOpen,
  });

  const createServerAlert = useMutation({
    mutationFn: (data: { market_id: number; symbol: string; condition: string; target_price: number }) =>
      api.post('/api/price-alerts', data).then((r) => r.data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['server-price-alerts'] }),
  });

  const deleteServerAlert = useMutation({
    mutationFn: (id: number) => api.delete(`/api/price-alerts/${id}`).then((r) => r.data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['server-price-alerts'] }),
  });

  const handleAdd = () => {
    if (price <= 0 || !config) return;
    // Always add to local store (browser notifications)
    addAlert({ marketId, symbol: config.symbol, targetPrice: price, direction });
    // Also save to server if authenticated (enables Telegram notifications)
    if (token) {
      createServerAlert.mutate({
        market_id: marketId,
        symbol: config.symbol,
        condition: direction,
        target_price: price,
      });
    }
    setPrice(0);
  };

  const activeAlerts = alerts.filter((a) => !a.triggered);
  const triggeredAlerts = alerts.filter((a) => a.triggered);
  const activeServerAlerts = serverAlerts.filter((a) => a.status === 'active');
  const triggeredServerAlerts = serverAlerts.filter((a) => a.status === 'triggered');

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Price Alerts">
      <div className="space-y-4">
        {/* Add alert form */}
        <div className="space-y-2">
          <div className="flex gap-2">
            <select
              value={marketId}
              onChange={(e) => setMarketId(Number(e.target.value))}
              className="input-field text-sm flex-1"
            >
              {MARKET_IDS.map((id) => (
                <option key={id} value={id}>{MARKETS[id].symbol}</option>
              ))}
            </select>
            <select
              value={direction}
              onChange={(e) => setDirection(e.target.value as 'above' | 'below')}
              className="input-field text-sm w-24"
            >
              <option value="above">Above</option>
              <option value="below">Below</option>
            </select>
          </div>
          <div className="flex gap-2">
            <input
              type="number"
              value={price || ''}
              onChange={(e) => setPrice(Number(e.target.value))}
              placeholder={market ? `Current: ${market.mark_price}` : 'Price'}
              step={1 / 10 ** (config?.decimals ?? 2)}
              className="input-field text-sm flex-1"
            />
            <button onClick={handleAdd} disabled={price <= 0} className="btn-primary text-xs px-4">Add</button>
          </div>
        </div>

        {/* Active alerts */}
        {activeAlerts.length > 0 && (
          <div>
            <div className="text-xs text-text-secondary font-medium uppercase tracking-wider mb-2">Active ({activeAlerts.length})</div>
            <div className="space-y-1">
              {activeAlerts.map((a) => (
                <div key={a.id} className="flex items-center justify-between p-2 bg-bg-secondary rounded text-xs">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-text-primary">{a.symbol}</span>
                    <span className={clsx('font-medium', a.direction === 'above' ? 'text-success' : 'text-danger')}>
                      {a.direction === 'above' ? '>' : '<'} ${formatPrice(a.targetPrice, MARKETS[a.marketId]?.decimals ?? 2)}
                    </span>
                  </div>
                  <button onClick={() => removeAlert(a.id)} className="text-text-secondary hover:text-danger text-[10px]">Remove</button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Triggered */}
        {triggeredAlerts.length > 0 && (
          <div>
            <div className="text-xs text-text-secondary font-medium uppercase tracking-wider mb-2">Triggered ({triggeredAlerts.length})</div>
            <div className="space-y-1">
              {triggeredAlerts.map((a) => (
                <div key={a.id} className="flex items-center justify-between p-2 bg-bg-secondary/50 rounded text-xs opacity-60">
                  <span>{a.symbol} {a.direction === 'above' ? '>' : '<'} ${formatPrice(a.targetPrice, MARKETS[a.marketId]?.decimals ?? 2)}</span>
                  <button onClick={() => removeAlert(a.id)} className="text-text-secondary hover:text-danger text-[10px]">Clear</button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Server-side alerts (Telegram) */}
        {token && activeServerAlerts.length > 0 && (
          <div>
            <div className="text-xs text-text-secondary font-medium uppercase tracking-wider mb-2">Server Alerts / Telegram ({activeServerAlerts.length})</div>
            <div className="space-y-1">
              {activeServerAlerts.map((a) => (
                <div key={`srv-${a.id}`} className="flex items-center justify-between p-2 bg-accent/5 border border-accent/10 rounded text-xs">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-text-primary">{a.symbol}</span>
                    <span className={clsx('font-medium', a.condition === 'above' ? 'text-success' : 'text-danger')}>
                      {a.condition === 'above' ? '>' : '<'} ${formatPrice(a.target_price, MARKETS[a.market_id]?.decimals ?? 2)}
                    </span>
                    <span className="text-[9px] text-accent">TG</span>
                  </div>
                  <button onClick={() => deleteServerAlert.mutate(a.id)} className="text-text-secondary hover:text-danger text-[10px]">Remove</button>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeAlerts.length === 0 && triggeredAlerts.length === 0 && activeServerAlerts.length === 0 && (
          <div className="text-center py-4 text-xs text-text-secondary">No alerts set</div>
        )}
      </div>
    </Modal>
  );
}

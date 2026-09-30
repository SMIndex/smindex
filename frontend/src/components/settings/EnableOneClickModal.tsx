import { useState } from 'react';
import { useSignTypedData } from 'wagmi';
import { useAuth } from '@/hooks/useAuth';
import { useToast } from '@/components/common/Toast';
import { getStoredApiKey, enrollApiKey } from '@/lib/perplApiKey';

const dismissKey = (address: string) => `perpl_1ct_dismissed_${address.toLowerCase()}`;

/**
 * One-time prompt after wallet connect: offer to enroll a Perpl API key so
 * all trading actions become one-click (no wallet popups). Dismissable;
 * always available later in Settings.
 */
export default function EnableOneClickModal() {
  const { address, isConnected, isAuthenticated } = useAuth();
  const { signTypedDataAsync } = useSignTypedData();
  const toast = useToast();
  const [enrolling, setEnrolling] = useState(false);
  const [closed, setClosed] = useState(false);

  if (!isConnected || !isAuthenticated || !address || closed) return null;
  if (getStoredApiKey(address)) return null;
  try {
    if (localStorage.getItem(dismissKey(address))) return null;
  } catch {}

  const dismiss = () => {
    try { localStorage.setItem(dismissKey(address), '1'); } catch {}
    setClosed(true);
  };

  const handleEnable = async () => {
    setEnrolling(true);
    try {
      await enrollApiKey({ address, signTypedDataAsync });
      toast.success('One-click trading enabled — no more wallet popups for orders');
      setClosed(true);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || err?.message || 'Enrollment failed');
    } finally {
      setEnrolling(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }}>
      <div className="card max-w-md w-full space-y-4">
        <h2 className="text-lg font-semibold text-text-primary">Enable One-Click Trading</h2>
        <p className="text-sm text-text-secondary">
          Sign one message to authorize this terminal with a Perpl API key. After that you can
          open, close, cancel and edit orders instantly — no wallet popups on every trade.
        </p>
        <ul className="text-xs text-text-secondary space-y-1 list-disc pl-4">
          <li>The secret key is generated and stored only in your browser.</li>
          <li>Withdrawals are never possible via API keys — trading only.</li>
          <li>Revoke anytime from Settings; the key is then deleted on Perpl too.</li>
          <li>For the smoothest experience, also enable one-click trading in your Perpl settings at app.perpl.xyz.</li>
        </ul>
        <div className="flex items-center gap-3 pt-1">
          <button onClick={handleEnable} disabled={enrolling} className="btn-primary text-sm">
            {enrolling ? 'Waiting for wallet signature…' : 'Enable now (1 signature)'}
          </button>
          <button onClick={dismiss} className="text-sm text-text-secondary hover:underline">
            Maybe later
          </button>
        </div>
      </div>
    </div>
  );
}

import { useState } from 'react';
import { useSignTypedData } from 'wagmi';
import { useAuth } from '@/hooks/useAuth';
import { useToast } from '@/components/common/Toast';
import {
  getStoredApiKey, enrollApiKey, importApiKey, revokeApiKey, clearStoredApiKey,
  type StoredApiKey,
} from '@/lib/perplApiKey';
import { disconnectPerpl, openTradingWsApiKey, getSession } from '@/lib/perplTrading';

export default function OneClickTradingCard() {
  const { address, isConnected } = useAuth();
  const { signTypedDataAsync } = useSignTypedData();
  const toast = useToast();

  const [key, setKey] = useState<StoredApiKey | null>(() => getStoredApiKey(address));
  const [busy, setBusy] = useState<'enroll' | 'revoke' | 'import' | 'test' | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [importToken, setImportToken] = useState('');
  const [importSecret, setImportSecret] = useState('');

  if (!isConnected || !address) return null;
  // Keep state in sync when the wallet changes
  if (key && key.address !== address.toLowerCase()) setKey(getStoredApiKey(address));

  const handleEnroll = async () => {
    setBusy('enroll');
    try {
      const stored = await enrollApiKey({ address, signTypedDataAsync });
      setKey(stored);
      toast.success('One-click trading enabled — no more wallet popups for orders');
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || err?.message || 'Enrollment failed');
    } finally {
      setBusy(null);
    }
  };

  const handleImport = async () => {
    if (!importToken.trim() || !importSecret.trim()) {
      toast.error('Paste both the API key token and the secret key');
      return;
    }
    setBusy('import');
    try {
      const stored = await importApiKey(address, importToken, importSecret);
      setKey(stored);
      setImportToken('');
      setImportSecret('');
      setShowImport(false);
      toast.success('API key imported — one-click trading enabled');
    } catch (err: any) {
      toast.error(err?.message || 'Import failed');
    } finally {
      setBusy(null);
    }
  };

  const handleRevoke = async () => {
    if (!key) return;
    if (!confirm('Revoke this API key? It will be deleted on Perpl and one-click trading will stop working until you enroll a new key.')) return;
    setBusy('revoke');
    try {
      await revokeApiKey(key);
      disconnectPerpl();
      setKey(null);
      toast.success('API key revoked and deleted on Perpl');
    } catch (err: any) {
      toast.error(
        (err?.response?.data?.detail || err?.message || 'Revoke failed') +
        ' — you can also delete it at app.perpl.xyz/apikeys',
      );
    } finally {
      setBusy(null);
    }
  };

  const handleTest = async () => {
    setBusy('test');
    try {
      await openTradingWsApiKey(address);
      const s = getSession();
      toast.success(`One-click connection OK${s.accountId ? ` (account #${s.accountId})` : ''} — orders will place without wallet popups`);
    } catch (err: any) {
      toast.error(`One-click sign-in FAILED: ${err?.message || 'unknown error'}`);
    } finally {
      setBusy(null);
    }
  };

  const handleForget = () => {
    if (!key) return;
    if (!confirm('Remove the key from this device only? It stays active on Perpl until revoked there.')) return;
    clearStoredApiKey(key.address);
    disconnectPerpl();
    setKey(null);
    toast.success('Key removed from this device');
  };

  return (
    <div className="card">
      <h2 className="text-lg font-semibold text-text-primary mb-2">One-Click Trading (API Key)</h2>
      <p className="text-sm text-text-secondary mb-4">
        Authorize this terminal with a Perpl API key: sign once, then open, close, cancel and
        edit orders instantly — no wallet popups. The secret key never leaves your browser, and
        withdrawals are never possible via API keys. Tip: also enable one-click trading in your
        Perpl settings at app.perpl.xyz for the smoothest experience.
      </p>

      {key ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-success" />
            <span className="text-sm text-success font-medium">Enabled</span>
            <span className="text-xs text-text-secondary ml-1">
              {key.label} · created {new Date(key.createdAt).toLocaleDateString()}
              {key.scopeMask === 1 ? ' · read-only' : ''}
            </span>
          </div>
          <div className="text-[10px] text-text-secondary font-mono break-all">
            Public key: {key.pubHex}
          </div>
          <div className="flex items-center gap-4">
            <button onClick={handleTest} disabled={busy === 'test'} className="text-xs text-accent hover:underline">
              {busy === 'test' ? 'Testing…' : 'Test connection'}
            </button>
            <button onClick={handleRevoke} disabled={busy === 'revoke'} className="text-xs text-danger hover:underline">
              {busy === 'revoke' ? 'Revoking…' : 'Revoke key (deletes on Perpl)'}
            </button>
            <button onClick={handleForget} className="text-xs text-text-secondary hover:underline">
              Remove from this device only
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <button onClick={handleEnroll} disabled={busy === 'enroll'} className="btn-primary text-sm">
            {busy === 'enroll' ? 'Waiting for wallet signature…' : 'Enable One-Click Trading'}
          </button>
          <div>
            <button onClick={() => setShowImport((s) => !s)} className="text-xs text-accent hover:underline">
              {showImport ? 'Hide manual import' : 'Or paste an existing key from app.perpl.xyz/apikeys'}
            </button>
          </div>
          {showImport && (
            <div className="space-y-2">
              <input
                type="text"
                value={importToken}
                onChange={(e) => setImportToken(e.target.value)}
                className="input-field w-full text-sm font-mono"
                placeholder="API key token (X-API-Key)"
              />
              <input
                type="password"
                value={importSecret}
                onChange={(e) => setImportSecret(e.target.value)}
                className="input-field w-full text-sm font-mono"
                placeholder="Ed25519 secret key (hex)"
              />
              <button onClick={handleImport} disabled={busy === 'import'} className="btn-primary text-xs">
                {busy === 'import' ? 'Importing…' : 'Import Key'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

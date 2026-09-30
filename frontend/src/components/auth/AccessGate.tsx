import { useState, useEffect } from 'react';
import api from '@/lib/api';

const STORAGE_KEY = 'perpl_access_verified';

export default function AccessGate({ children }: { children: React.ReactNode }) {
  const [verified, setVerified] = useState(false);
  const [checking, setChecking] = useState(true);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'true') {
      setVerified(true);
    }
    setChecking(false);
  }, []);

  const handleSubmit = async () => {
    if (!code.trim()) return;
    setLoading(true);
    setError('');

    try {
      const resp = await api.post('/api/verify-access', { code: code.trim() });
      if (resp.data.valid) {
        localStorage.setItem(STORAGE_KEY, 'true');
        setVerified(true);
      } else {
        setError('Invalid access code');
      }
    } catch {
      setError('Failed to verify');
    } finally {
      setLoading(false);
    }
  };

  if (checking) return null;

  if (verified) return <>{children}</>;

  return (
    <div className="min-h-screen bg-bg-primary flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-accent to-accent/60 flex items-center justify-center mx-auto mb-4 shadow-lg shadow-accent/25">
            <span className="text-white font-black text-2xl">P</span>
          </div>
          <h1 className="text-2xl font-bold text-text-primary">SMINDEX</h1>
          <p className="text-sm text-text-secondary mt-1">Enter access code to continue</p>
        </div>

        <div className="bg-gradient-to-br from-bg-secondary to-bg-card border border-text-secondary/10 rounded-xl p-6 space-y-4">
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1.5">Access Code</label>
            <input
              type="text"
              value={code}
              onChange={(e) => { setCode(e.target.value); setError(''); }}
              onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
              placeholder="Enter your access code"
              className="input-field text-center text-lg tracking-widest uppercase"
              autoFocus
            />
          </div>

          {error && (
            <div className="text-xs text-danger text-center bg-danger/10 rounded-lg py-2">{error}</div>
          )}

          <button
            onClick={handleSubmit}
            disabled={loading || !code.trim()}
            className="w-full py-3 rounded-xl bg-gradient-to-r from-accent to-accent/80 text-white font-semibold text-sm hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {loading ? 'Verifying...' : 'Enter'}
          </button>
        </div>

        <p className="text-[10px] text-text-secondary/50 text-center mt-4">
          Copy trading terminal for Perpl perpetual futures on Monad
        </p>
      </div>
    </div>
  );
}

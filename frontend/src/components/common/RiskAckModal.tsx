import { useEffect, useState } from 'react';
import { subscribeRiskAck, acceptRiskAck, declineRiskAck } from '@/lib/riskAck';

// The one-time risk notice (beta). Mounted once at the app root.
export default function RiskAckModal() {
  const [open, setOpen] = useState(false);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => subscribeRiskAck((s) => { setOpen(s.open); if (s.open) { setChecked(false); setErr(null); } }), []);
  if (!open) return null;
  const accept = async () => {
    setBusy(true); setErr(null);
    try { await acceptRiskAck(); }
    catch (e: any) {
      setErr(e?.response?.status === 401 ? 'Connect your wallet first, then try again.' : 'Could not save your answer. Check your connection and try again.');
    } finally { setBusy(false); }
  };
  return (
    <div className="pmodal fixed inset-0 z-[130] flex items-center justify-center p-4" role="presentation">
      <div className="pmodal-back absolute inset-0 bg-black/60" onClick={declineRiskAck} />
      <div role="dialog" aria-modal="true" aria-labelledby="riskack-title"
           className="pmodal-panel relative w-full max-w-md bg-bg-card border border-text-secondary/20 rounded-xl shadow-2xl">
        <div className="pmodal-head px-5 py-4 border-b border-text-secondary/10">
          <h2 id="riskack-title" className="text-lg font-semibold text-text-primary">Before you trade</h2>
        </div>
        <div className="pmodal-body p-5 space-y-3 text-sm text-text-primary">
          <p>SMINDEX is in beta. Please read this once before placing real orders.</p>
          <ul className="list-disc pl-5 space-y-1.5 text-text-secondary">
            <li>Trading perpetual futures is risky. With leverage you can lose your whole margin quickly.</li>
            <li>Copy trading and auto-copy can lose money. A trader you copy can lose, and copies can fill at worse prices than theirs.</li>
            <li>Past performance is not a guarantee of future results. Rankings and scores describe the past.</li>
            <li>You are responsible for every order you confirm. SMINDEX never holds your funds.</li>
          </ul>
          <label className="flex items-start gap-2 min-h-[44px] cursor-pointer pt-1">
            <input type="checkbox" className="mt-0.5 w-5 h-5" checked={checked} onChange={() => setChecked(!checked)} />
            <span>I understand these risks.</span>
          </label>
          {err && <div className="text-[12.5px] text-danger">{err}</div>}
          <div className="flex gap-2 pt-1">
            <button onClick={declineRiskAck} className="flex-1 min-h-[44px] rounded-lg border border-text-secondary/20 text-text-primary">Not now</button>
            <button onClick={accept} disabled={!checked || busy}
                    className="flex-1 min-h-[44px] rounded-lg bg-accent text-white font-semibold disabled:opacity-50">
              {busy ? 'Saving…' : 'Continue'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

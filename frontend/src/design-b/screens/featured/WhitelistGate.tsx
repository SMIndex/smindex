import { useEffect, useState, type FormEvent } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { requestWhitelist } from '@/lib/featuredApi';
import { shortAddr } from '@/lib/featuredView';

// Whitelist gate (design/Whitelist.dc.html): the real page layout rendered
// from /preview sits behind a blur with no pointer events; this card sits on
// top. On a phone the card is a bottom sheet (featured.css).

const WALLET_RE = /^0x[0-9a-fA-F]{40}$/;

export default function WhitelistGate({ onRequested }: { onRequested?: () => void }) {
  const { address, isAuthenticated, login } = useAuth();
  const [email, setEmail] = useState('');
  const [handle, setHandle] = useState('');
  const [wallet, setWallet] = useState('');
  const [hp, setHp] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const locked = !!address;
  useEffect(() => { if (address) setWallet(address); }, [address]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email.trim())) { setErr('Enter a valid email address.'); return; }
    if (!/^@?[A-Za-z0-9_]{1,15}$/.test(handle.trim())) { setErr('X handle: 1 to 15 letters, digits or underscores.'); return; }
    if (!WALLET_RE.test(wallet.trim())) { setErr('Enter a valid wallet address (0x and 40 hex characters).'); return; }
    setBusy(true);
    try {
      const r = await requestWhitelist({ email: email.trim(), xHandle: handle.trim(), wallet: wallet.trim(), hp });
      if (r.status === 'already_whitelisted') setDone('This wallet already has access. Sign in with it to open the page.');
      else if (r.status === 'already_requested') setDone('Already requested. You will hear from us on X or by email.');
      else setDone('Request received. You will hear from us on X or by email.');
      onRequested?.();
    } catch (ex: any) {
      setErr(ex?.response?.data?.detail || 'Could not send the request. Try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="gcard" role="dialog" aria-label="Request access to Featured traders">
      <span className="chip chip-warn">PRIVATE BETA</span>
      <h2>Featured traders is in private beta</h2>
      <p>Wallets that make money by trading, with every check shown, and a Copy button on each. Request access and I will whitelist your wallet.</p>
      {!done ? (
        <form onSubmit={submit}>
          <div><label className="lab" htmlFor="wl-email">Email</label><input className="inp" id="wl-email" type="email" placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" /></div>
          <div><label className="lab" htmlFor="wl-x">X handle</label><input className="inp" id="wl-x" type="text" placeholder="@handle" value={handle} onChange={(e) => setHandle(e.target.value)} autoComplete="off" /></div>
          <div>
            <label className="lab" htmlFor="wl-wallet">Wallet address</label>
            <input className="inp mono" id="wl-wallet" type="text" value={wallet} readOnly={locked} placeholder="0x..."
              onChange={(e) => setWallet(e.target.value)} spellCheck={false} />
            {locked
              ? <div className="sub" style={{ marginTop: 4 }}>Taken from your connected wallet{address ? ` (${shortAddr(address)})` : ''}.</div>
              : <div className="sub" style={{ marginTop: 4 }}>
                  Or <button type="button" className="btn sm ghost" style={{ padding: 0, height: 'auto', color: 'var(--accent-text)' }} onClick={() => login()}>connect your wallet</button> to fill it in.
                </div>}
          </div>
          <label className="hp" aria-hidden="true">Company<input type="text" tabIndex={-1} autoComplete="off" value={hp} onChange={(e) => setHp(e.target.value)} /></label>
          <div className="sub">You will get one message when access is granted. Nothing else.</div>
          <button type="submit" className="submit" disabled={busy}>{busy ? 'Sending…' : 'Request access'}</button>
          {err && <div className="err" role="alert">{err}</div>}
          {isAuthenticated && <div className="sub">Signed in. The page unlocks on your next load once approved.</div>}
        </form>
      ) : (
        <div className="ok" role="status">{done}</div>
      )}
    </div>
  );
}

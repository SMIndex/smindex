import { useState } from 'react';
import { useLocation } from 'react-router-dom';
import api from '@/lib/api';

// Beta basics (2026-10-07): plain-language Terms and Privacy pages, and the
// feedback form (Settings + /feedback). Text reviewed by the owner before launch.

const UPDATED = '7 October 2026';

export function TermsB() {
  return (
    <div className="screen" style={{ maxWidth: 760 }}>
      <div className="head"><div><h1>Terms of use</h1><p>Beta · last updated {UPDATED}</p></div></div>
      <div className="card legal">
        <h2>What SMINDEX is</h2>
        <p>SMINDEX shows public trading data from Hyperliquid and Perpl, and lets you place orders and copy traders on Perpl from your own wallet. It is in beta: features can change, break or be removed.</p>
        <h2>Not financial advice</h2>
        <p>Scores, rankings and alerts describe what wallets did in the past. They are information, not a recommendation to buy or sell, and past performance is not a guarantee of future results.</p>
        <h2>Your trades are yours</h2>
        <p>Every manual order is signed by you and placed on Perpl. Copy trades are confirmed by you one by one. Auto-copy places orders only after you turn it on for a trader, set its limits and add a trade-only Perpl key. You can pause or stop it at any time. Trading perpetual futures with leverage can lose your whole margin, and copying a trader can lose money.</p>
        <h2>Your funds</h2>
        <p>SMINDEX never holds your funds. Withdrawals always need your own wallet signature on Perpl; the auto-copy key cannot withdraw or move funds.</p>
        <h2>No warranty</h2>
        <p>SMINDEX is provided as is. Data can be late, incomplete or wrong, and venues can be slow or unavailable. Check positions on the venue before acting on anything shown here.</p>
        <h2>Fair use</h2>
        <p>Do not try to break, overload or scrape the service beyond normal use. Access can be limited or removed for abuse.</p>
      </div>
    </div>
  );
}

export function PrivacyB() {
  return (
    <div className="screen" style={{ maxWidth: 760 }}>
      <div className="head"><div><h1>Privacy</h1><p>Beta · last updated {UPDATED}</p></div></div>
      <div className="card legal">
        <h2>What we store about you</h2>
        <ul>
          <li><b>Wallet address</b>, when you sign in with your wallet.</li>
          <li><b>Your settings</b>: watchlist, copy and auto-copy settings, alert preferences, and the trades and copy orders you make through SMINDEX.</li>
          <li><b>Telegram link</b>, if you connect alerts: your Telegram chat id, so we can send the alerts you choose.</li>
          <li><b>Auto-copy key</b>, if you add one: stored encrypted, used only by the auto-copy worker to place, change and cancel orders. It can never withdraw. You can delete it in Settings at any time.</li>
          <li><b>Feedback</b> you send us, with your wallet address if you are signed in.</li>
        </ul>
        <h2>What we do not store</h2>
        <p>No names, emails or passwords, and never your wallet's private key or seed phrase.</p>
        <h2>Public data</h2>
        <p>Positions, trades and balances shown for any wallet come from public venue and blockchain data, not from you.</p>
        <h2>Deleting your data</h2>
        <p>Remove your auto-copy key in Settings and unlink Telegram there. To delete everything tied to your wallet, send a request through the feedback form while signed in with that wallet.</p>
      </div>
    </div>
  );
}

export function FeedbackForm({ compact }: { compact?: boolean }) {
  const location = useLocation();
  const [msg, setMsg] = useState('');
  const [contact, setContact] = useState('');
  const [state, setState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');
  const [err, setErr] = useState('');
  const send = async () => {
    setState('sending'); setErr('');
    try {
      await api.post('/api/feedback', { message: msg, contact: contact || null, page: location.pathname });
      setState('sent'); setMsg(''); setContact('');
    } catch (e: any) {
      setState('error');
      setErr(e?.response?.data?.detail || 'Could not send. Try again in a moment.');
    }
  };
  return (
    <div className="card" style={{ maxWidth: 720, marginTop: compact ? 12 : 0 }}>
      <h2>Send feedback</h2>
      <div className="sub">Found a bug, something confusing, or a feature you want? It goes straight to the team.</div>
      <textarea className="input" rows={4} value={msg} onChange={(e) => setMsg(e.target.value)} maxLength={4000}
                placeholder="What happened, and on which page?" aria-label="Feedback message"
                style={{ width: '100%', marginTop: 12, minHeight: 96, resize: 'vertical' }} />
      <input className="input" value={contact} onChange={(e) => setContact(e.target.value)} maxLength={120}
             placeholder="Telegram handle (optional, if you want a reply)" aria-label="Contact (optional)"
             style={{ width: '100%', marginTop: 8, minHeight: 44 }} />
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
        <button className="btn primary" disabled={msg.trim().length < 3 || state === 'sending'} onClick={send}>
          {state === 'sending' ? 'Sending…' : 'Send feedback'}
        </button>
        {state === 'sent' && <span className="long-c" style={{ fontSize: 13 }}>Thanks, we got it.</span>}
        {state === 'error' && <span className="short-c" style={{ fontSize: 13 }}>{err}</span>}
      </div>
    </div>
  );
}

export function FeedbackB() {
  return (
    <div className="screen">
      <div className="head"><div><h1>Feedback</h1><p>SMINDEX is in beta. Tell us what to fix.</p></div></div>
      <FeedbackForm />
    </div>
  );
}

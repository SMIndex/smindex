import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { adminAddWallet, adminDecide, adminFeaturedStatus, adminRemoveWallet, adminRunFeatured, adminWhitelist } from '@/lib/featuredApi';
import { shortAddr } from '@/lib/featuredView';
import './featured.css';

// /admin/featured-whitelist: pending, approved and rejected requests with
// search; approve / reject call the same code as the Telegram buttons. The
// server enforces ADMIN_ADDRESSES; this page is only the affordance.

export default function FeaturedAdminB() {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const [status, setStatus] = useState<string>('pending');
  const [q, setQ] = useState('');
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [addr, setAddr] = useState('');
  const [job, setJob] = useState<any>(null);
  const load = useCallback(() => {
    adminWhitelist(status === 'all' ? undefined : status, q || undefined).then((d) => { setData(d); setErr(null); })
      .catch((e) => setErr(e?.response?.status === 403 ? 'Admin wallets only.' : (e?.response?.data?.detail || 'Could not load')));
    adminFeaturedStatus().then(setJob).catch(() => {});
  }, [status, q]);
  useEffect(() => { load(); }, [load, isAuthenticated]);
  const decide = async (id: number, approve: boolean) => {
    const note = approve ? undefined : (window.prompt('Reason (optional)') ?? undefined);
    await adminDecide(id, approve, note).catch((e) => setErr(e?.response?.data?.detail || 'Failed'));
    load();
  };
  return (
    <div className="screen ft">
      <div className="ft-head">
        <div>
          <button className="btn sm ghost" style={{ paddingLeft: 0 }} onClick={() => navigate('/copy/featured/wallets')}>← Featured traders</button>
          <h1>Featured traders whitelist</h1>
          <div className="sub">Requests from the gate. Approve adds the wallet; the page unlocks on the visitor's next load.{data && !data.admin_chat_configured ? ' ADMIN_TELEGRAM_CHAT_ID is not set: no Telegram message is sent, requests only show here.' : ''}</div>
        </div>
        {job && <div className="sub">Daily job: {job.job?.running ? 'running' : 'idle'} · weight today {job.weight_today} · latest {job.latest?.map((l: any) => `${l.venue} ${l.qualified} qualified of ${l.rows}`).join(', ')}
          <button className="btn sm" style={{ marginLeft: 8 }} onClick={() => adminRunFeatured().then(() => setTimeout(load, 1500))}>Run now</button></div>}
      </div>
      {err && <div className="ft-empty" style={{ color: 'var(--short)' }}>{err}</div>}
      <div className="ft-filters">
        <div className="seg">{['pending', 'approved', 'rejected', 'all'].map((s) => <button key={s} aria-pressed={status === s} onClick={() => setStatus(s)}>{s[0].toUpperCase() + s.slice(1)}</button>)}</div>
        <input className="dinput" placeholder="Search wallet, email or handle" value={q} onChange={(e) => setQ(e.target.value)} style={{ minWidth: 260 }} />
        <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 6 }}>
          <input className="dinput mono" placeholder="0x... add directly" value={addr} onChange={(e) => setAddr(e.target.value)} style={{ width: 300 }} />
          <button className="btn" onClick={() => adminAddWallet(addr.trim()).then(() => { setAddr(''); load(); }).catch((e) => setErr(e?.response?.data?.detail || 'Failed'))}>Add wallet</button>
        </span>
      </div>
      <div className="ft-table-wrap">
        <table className="ft-table">
          <thead><tr><th>#</th><th>Wallet</th><th>Email</th><th>X</th><th>Status</th><th>Requested</th><th>Decided</th><th>Note</th><th></th></tr></thead>
          <tbody>
            {(data?.requests ?? []).map((r: any) => (
              <tr key={r.id}>
                <td className="sub">{r.id}</td><td className="mono" title={r.wallet}>{shortAddr(r.wallet)}</td><td>{r.email}</td><td>@{r.x_handle}</td>
                <td><span className={`chip ${r.status === 'approved' ? 'chip-ok' : r.status === 'rejected' ? 'chip-fail' : 'chip-warn'}`}>{r.status}</span></td>
                <td>{r.created_at?.replace('T', ' ').slice(0, 16)}</td><td>{r.decided_at?.replace('T', ' ').slice(0, 16) ?? ''}{r.decided_by ? ` by ${r.decided_by.slice(0, 10)}` : ''}</td><td>{r.note ?? ''}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <div className="acts">
                    {r.status !== 'approved' && <button className="btn primary" onClick={() => decide(r.id, true)}>Approve</button>}
                    {r.status !== 'rejected' && <button className="btn" onClick={() => decide(r.id, false)}>Reject</button>}
                  </div>
                </td>
              </tr>
            ))}
            {data && data.requests.length === 0 && <tr><td colSpan={9} className="sub">No {status === 'all' ? '' : status} requests.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="ft-card">
        <div className="lab">Whitelisted wallets ({data?.whitelist?.length ?? 0})</div>
        <div style={{ overflowX: 'auto', marginTop: 8 }}>
          <table className="ft-table">
            <thead><tr><th>Wallet</th><th>Approved</th><th>By</th><th>Source</th><th></th></tr></thead>
            <tbody>
              {(data?.whitelist ?? []).map((w: any) => (
                <tr key={w.wallet}><td className="mono">{w.wallet}</td><td>{w.approved_at?.replace('T', ' ').slice(0, 16)}</td><td>{w.approved_by?.slice(0, 12)}</td><td>{w.source}</td>
                  <td><button className="btn" onClick={() => { if (window.confirm(`Remove ${shortAddr(w.wallet)} from the whitelist?`)) adminRemoveWallet(w.wallet).then(load); }}>Remove</button></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {job?.runs?.length ? (
        <div className="ft-card">
          <div className="lab">Job runs</div>
          <div style={{ overflowX: 'auto', marginTop: 8 }}>
            <table className="ft-table">
              <thead><tr><th>#</th><th>Job</th><th>Started</th><th>Finished</th><th>OK</th><th>Requests</th><th>Weight</th><th>Notes</th></tr></thead>
              <tbody>{job.runs.map((r: any) => <tr key={r.id}><td>{r.id}</td><td>{r.job}</td><td>{r.started_at?.replace('T', ' ')}</td><td>{r.finished_at?.replace('T', ' ') ?? 'running'}</td><td>{r.ok == null ? '' : r.ok ? 'yes' : 'no'}</td><td>{r.requests}</td><td>{r.weight}</td><td style={{ whiteSpace: 'normal' }}>{r.notes}</td></tr>)}</tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}

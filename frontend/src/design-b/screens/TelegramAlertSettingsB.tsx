import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '@/lib/api';

// Telegram alert settings (spec Part 3.5). Backed by GET/PUT /api/telegram/prefs,
// POST /api/telegram/test, GET /api/telegram/alert-log. Every toggle saves the
// moment it is clicked; parameters save on blur / Enter. Producers read the
// saved value on their next event (no restart).

type Alert = {
  type: string; label: string; enabled: boolean; params: Record<string, any>;
  defaults: Record<string, any>; default_enabled: boolean; delay: string; critical: boolean;
};
type Prefs = { groups: { id: string; label: string; alerts: Alert[] }[]; global: Record<string, any> };

const PARAM_LABELS: Record<string, string> = {
  margin_use_pct: 'alert at margin use %', above: 'above', below: 'below',
  min_notional_usd: 'min position $', hour: 'hour (0-23)', assets: 'assets (comma-separated, empty = what you hold)',
};
const HOURS = Array.from({ length: 24 }, (_, h) => h);

function ParamInput({ name, value, onSave, disabled }: { name: string; value: any; onSave: (v: any) => void; disabled: boolean }) {
  const isList = Array.isArray(value);
  const [draft, setDraft] = useState<string>(isList ? value.join(', ') : String(value ?? ''));
  const commit = () => {
    const next = isList ? draft.split(',').map((s) => s.trim()).filter(Boolean) : Number(draft);
    if (!isList && !Number.isFinite(next)) return;
    const same = isList ? JSON.stringify(next) === JSON.stringify(value) : next === value;
    if (!same) onSave(next);
  };
  return (
    <label className="dim" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5, marginRight: 12 }}>
      {PARAM_LABELS[name] ?? name}
      <input className="input" style={{ width: isList ? 220 : 90, minHeight: 32 }} value={draft} disabled={disabled}
             inputMode={isList ? 'text' : 'decimal'} aria-label={PARAM_LABELS[name] ?? name}
             onChange={(e) => setDraft(e.target.value)} onBlur={commit}
             onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }} />
    </label>
  );
}

export default function TelegramAlertSettingsB({ linked }: { linked: boolean }) {
  const qc = useQueryClient();
  const prefs = useQuery<Prefs>({ queryKey: ['telegram-alert-prefs'], queryFn: () => api.get('/api/telegram/prefs').then((r) => r.data) });
  const log = useQuery({ queryKey: ['telegram-alert-log'], queryFn: () => api.get('/api/telegram/alert-log?limit=8').then((r) => r.data), enabled: linked, refetchInterval: 15000 });
  const [err, setErr] = useState<string | null>(null);
  const [testMsg, setTestMsg] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: ({ type, body }: { type: string; body: { enabled?: boolean; params?: Record<string, any> } }) =>
      api.put(`/api/telegram/prefs/${type}`, body).then((r) => r.data),
    onSuccess: () => { setErr(null); qc.invalidateQueries({ queryKey: ['telegram-alert-prefs'] }); },
    onError: (e: any) => setErr(e?.response?.data?.detail || 'Saving failed. Try again.'),
  });
  const test = useMutation({
    mutationFn: () => api.post('/api/telegram/test').then((r) => r.data),
    onSuccess: (d) => { setTestMsg(d.status === 'queued' ? 'Sent — check Telegram.' : `Not sent (${d.status}).`); qc.invalidateQueries({ queryKey: ['telegram-alert-log'] }); },
    onError: (e: any) => setTestMsg(e?.response?.data?.detail || 'Test failed.'),
  });

  const g = prefs.data?.global ?? {};
  const setGlobal = (params: Record<string, any>) => save.mutate({ type: '_global', body: { params } });
  const deviceTz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { return 'UTC'; } })();

  return (
    <div className="card" style={{ maxWidth: 720, marginTop: 12 }}>
      <h2>Alert settings</h2>
      <div className="sub">
        Choose what reaches your Telegram{linked ? '' : ' — alerts deliver once Telegram is linked above'}. Changes apply to the next event.
      </div>
      {prefs.isError && <div className="banner short" style={{ marginTop: 8 }}>Alert settings unavailable — retrying.</div>}
      {err && <div className="banner short" style={{ marginTop: 8 }}>{err}</div>}
      {prefs.isLoading && <div className="dim" style={{ marginTop: 10, fontSize: 12.5 }}>Loading…</div>}

      {prefs.data?.groups.map((grp) => (
        <div key={grp.id} style={{ marginTop: 14 }}>
          <div className="dim" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 4 }}>{grp.label}</div>
          {grp.alerts.map((a) => (
            <div key={a.type} className="kv" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 4 }}>
              <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, cursor: 'pointer', minHeight: 44 }}>
                <span>{a.label}{a.critical && <span className="dim" style={{ fontSize: 11 }}> · passes quiet hours</span>}</span>
                <input type="checkbox" checked={a.enabled} disabled={save.isPending} aria-label={a.label}
                       style={{ width: 22, height: 22 }}
                       onChange={() => save.mutate({ type: a.type, body: { enabled: !a.enabled } })} />
              </label>
              {a.enabled && Object.keys(a.params).length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', rowGap: 6 }}>
                  {Object.entries(a.params).map(([k, v]) => (
                    <ParamInput key={`${a.type}:${k}:${JSON.stringify(v)}`} name={k} value={v} disabled={save.isPending}
                                onSave={(nv) => save.mutate({ type: a.type, body: { params: { [k]: nv } } })} />
                  ))}
                </div>
              )}
              <div className="dim" style={{ fontSize: 11.5 }}>{a.delay}</div>
            </div>
          ))}
        </div>
      ))}

      {prefs.data && (
        <div style={{ marginTop: 16 }}>
          <div className="dim" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 4 }}>Delivery</div>
          <label className="kv" style={{ cursor: 'pointer', minHeight: 44 }}>
            <span>Quiet hours <span className="dim" style={{ fontSize: 11.5 }}>(only failures and liquidation warnings get through)</span></span>
            <input type="checkbox" checked={!!g.quiet_enabled} disabled={save.isPending} aria-label="Quiet hours"
                   style={{ width: 22, height: 22 }} onChange={() => setGlobal({ quiet_enabled: !g.quiet_enabled })} />
          </label>
          {g.quiet_enabled && (
            <div className="kv" style={{ gap: 8, justifyContent: 'flex-start', flexWrap: 'wrap' }}>
              <span className="dim" style={{ fontSize: 12.5 }}>from</span>
              <select className="input" value={g.quiet_start} onChange={(e) => setGlobal({ quiet_start: Number(e.target.value) })} aria-label="Quiet from">
                {HOURS.map((h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
              </select>
              <span className="dim" style={{ fontSize: 12.5 }}>to</span>
              <select className="input" value={g.quiet_end} onChange={(e) => setGlobal({ quiet_end: Number(e.target.value) })} aria-label="Quiet to">
                {HOURS.map((h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
              </select>
            </div>
          )}
          <div className="kv" style={{ flexWrap: 'wrap', gap: 8 }}>
            <span>Timezone <span className="mono">{g.tz}</span></span>
            {deviceTz && deviceTz !== g.tz && (
              <button className="btn" style={{ minHeight: 44 }} onClick={() => setGlobal({ tz: deviceTz })}>Use this device ({deviceTz})</button>
            )}
          </div>
          <div className="kv">
            <span>Max messages per hour <span className="dim" style={{ fontSize: 11.5 }}>(extra alerts arrive as one digest)</span></span>
            <ParamInput key={`limit:${g.hourly_limit}`} name="hourly_limit" value={g.hourly_limit} disabled={save.isPending}
                        onSave={(v) => setGlobal({ hourly_limit: v })} />
          </div>
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 14, flexWrap: 'wrap' }}>
        <button className="btn primary" style={{ minHeight: 44 }} disabled={!linked || test.isPending} onClick={() => test.mutate()}>
          {test.isPending ? 'Sending…' : 'Send test message'}
        </button>
        {testMsg && <span className="dim" style={{ fontSize: 12.5 }}>{testMsg}</span>}
      </div>

      {linked && (log.data?.rows?.length ?? 0) > 0 && (
        <div style={{ marginTop: 14 }}>
          <div className="dim" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 4 }}>Recent alerts</div>
          {log.data.rows.map((r: any) => (
            <div key={r.id} className="kv" style={{ fontSize: 12.5 }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '62%' }}>
                {r.alert_type.replace(/_/g, ' ')} · <span className="dim">{r.preview.replace(/<[^>]+>/g, '').split('\n')[0]}</span>
              </span>
              <span className="mono dim">{r.status}{r.tg_message_id ? ` #${r.tg_message_id}` : ''}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

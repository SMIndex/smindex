import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { isEvmAddress, normAddress } from '@/lib/explorerApi';

// Top-bar / landing wallet search (Wallet Explorer Part 2). Validates a 0x
// address, lowercases, navigates to /wallet/:address. Lives outside the
// screen file so ShellB can render it without pulling the whole page chunk.
export default function ExplorerSearch({ compact }: { compact?: boolean }) {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [bad, setBad] = useState(false);
  const go = () => {
    if (!isEvmAddress(q)) { setBad(true); return; }
    setBad(false);
    navigate(`/wallet/${normAddress(q)}`);
    setQ('');
  };
  return (
    <div style={{ display: 'flex', gap: 6, width: compact ? undefined : '100%' }}>
      <input
        value={q}
        onChange={(e) => { setQ(e.target.value); setBad(false); }}
        onKeyDown={(e) => { if (e.key === 'Enter') go(); }}
        placeholder={compact ? '0x address…' : 'Look up any 0x wallet address'}
        aria-invalid={bad}
        aria-label="Wallet address search"
        style={{
          flex: 1, minWidth: compact ? 110 : 220, padding: compact ? '6px 9px' : '9px 12px',
          borderRadius: 8, border: `1px solid ${bad ? 'var(--short)' : 'var(--line)'}`,
          background: 'var(--s2)', color: 'var(--text)', fontSize: compact ? 12.5 : 13.5,
          fontFamily: 'inherit', outline: 'none',
        }}
      />
      <button className="btn sm" onClick={go}>Go</button>
    </div>
  );
}

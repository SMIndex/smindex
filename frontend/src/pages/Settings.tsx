import { useState, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/authStore';
import { useAuth } from '@/hooks/useAuth';
import api, { postLinkPerpl, postSetUsername, getMyPositions } from '@/lib/api';
import { shortenAddress } from '@/lib/formatters';
import { useToast } from '@/components/common/Toast';
import PositionCard from '@/components/dashboard/PositionCard';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorBoundary from '@/components/common/ErrorBoundary';
import OneClickTradingCard from '@/components/settings/OneClickTradingCard';

function TelegramCard() {
  const [linkUrl, setLinkUrl] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const queryClient = useQueryClient();

  const { data: tgStatus, refetch: refetchStatus } = useQuery({
    queryKey: ['telegram-status'],
    queryFn: () => api.get('/api/telegram/status').then((r) => r.data),
    refetchInterval: linkUrl ? 3000 : false, // Poll while waiting for link
  });

  // Stop polling once linked
  useEffect(() => {
    if (tgStatus?.linked && linkUrl) setLinkUrl('');
  }, [tgStatus?.linked, linkUrl]);

  const handleGenerateLink = async () => {
    setIsGenerating(true);
    try {
      const data = await api.post('/api/telegram/generate-link-code').then((r) => r.data);
      setLinkUrl(data.bot_url);
    } catch (err: any) {
      if (err?.response?.data?.detail === 'Telegram already linked. Unlink first.') {
        refetchStatus();
      }
    } finally {
      setIsGenerating(false);
    }
  };

  const handleUnlink = async () => {
    await api.delete('/api/telegram/unlink');
    setLinkUrl('');
    refetchStatus();
  };

  return (
    <div className="card">
      <h2 className="text-lg font-semibold text-text-primary mb-2">Telegram Notifications</h2>
      <p className="text-sm text-text-secondary mb-4">
        Get alerts when leaders you follow open or close trades. Reply to execute.
      </p>

      {tgStatus?.linked ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-success" />
            <span className="text-sm text-success font-medium">Linked</span>
            <span className="text-xs text-text-secondary ml-1">Chat ID: {tgStatus.chat_id_masked}</span>
          </div>
          <button onClick={handleUnlink} className="text-xs text-danger hover:underline">Unlink Telegram</button>
        </div>
      ) : linkUrl ? (
        <div className="space-y-3">
          <p className="text-xs text-text-secondary">Click the link below to open the bot and link your account:</p>
          <a href={linkUrl} target="_blank" rel="noopener noreferrer" className="block text-sm text-accent font-mono break-all hover:underline">
            {linkUrl}
          </a>
          <p className="text-[10px] text-text-secondary animate-pulse">Waiting for you to open the link...</p>
        </div>
      ) : (
        <button
          onClick={handleGenerateLink}
          disabled={isGenerating}
          className="btn-primary text-sm"
        >
          {isGenerating ? 'Generating...' : 'Link Telegram'}
        </button>
      )}
    </div>
  );
}

type McpToken = {
  id: number;
  label: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

function McpCard() {
  const [label, setLabel] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [newToken, setNewToken] = useState<string | null>(null);
  const [showSnippets, setShowSnippets] = useState(false);
  const queryClient = useQueryClient();
  const toast = useToast();

  const { data: tokens = [] } = useQuery<McpToken[]>({
    queryKey: ['mcp-tokens'],
    queryFn: () => api.get('/api/mcp-tokens').then((r) => r.data),
  });

  const activeTokens = tokens.filter((t) => !t.revoked_at);

  const handleCreate = async () => {
    const trimmed = label.trim();
    if (!trimmed) {
      toast.error('Label is required');
      return;
    }
    setIsCreating(true);
    try {
      const data = await api
        .post('/api/mcp-tokens', { label: trimmed })
        .then((r) => r.data);
      setNewToken(data.token);
      setLabel('');
      queryClient.invalidateQueries({ queryKey: ['mcp-tokens'] });
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to create token');
    } finally {
      setIsCreating(false);
    }
  };

  const handleRevoke = async (id: number) => {
    if (!confirm('Revoke this MCP token? Any client using it will lose access immediately.')) return;
    try {
      await api.delete(`/api/mcp-tokens/${id}`);
      queryClient.invalidateQueries({ queryKey: ['mcp-tokens'] });
      toast.success('Token revoked');
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to revoke');
    }
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success('Copied to clipboard');
    } catch {
      toast.error('Copy failed');
    }
  };

  const httpUrl = `${window.location.origin}/mcp/sse`;
  const stdioConfig = (token: string) =>
    JSON.stringify(
      {
        mcpServers: {
          'perpl-terminal': {
            command: 'python',
            args: ['-m', 'app.mcp.stdio'],
            cwd: '/path/to/perpl/backend',
            env: { PERPL_MCP_TOKEN: token },
          },
        },
      },
      null,
      2,
    );

  return (
    <div className="card">
      <h2 className="text-lg font-semibold text-text-primary mb-2">MCP Access (Claude)</h2>
      <p className="text-sm text-text-secondary mb-4">
        Generate a token to connect Claude Desktop, Claude Code, or Claude.ai to your Perpl
        Terminal. Claude will be able to read your positions, leaderboard, funding rates, and
        propose trades via deep links you click to confirm. The server never places orders directly.
      </p>

      {/* Newly generated token — show ONCE */}
      {newToken && (
        <div className="mb-4 p-3 rounded border border-warning/40 bg-warning/5 space-y-2">
          <p className="text-xs font-semibold text-warning">
            Save this token now — it will not be shown again.
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-xs font-mono bg-bg-secondary px-2 py-1.5 rounded break-all">
              {newToken}
            </code>
            <button
              onClick={() => copy(newToken)}
              className="text-xs px-2 py-1 rounded bg-accent text-white hover:opacity-90"
            >
              Copy
            </button>
          </div>
          <button
            onClick={() => {
              setNewToken(null);
              setShowSnippets(true);
            }}
            className="text-xs text-accent hover:underline"
          >
            I've saved it — show me how to wire it up →
          </button>
        </div>
      )}

      {/* Create form */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 sm:gap-3 mb-4">
        <input
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          className="input-field flex-1"
          placeholder="Token label (e.g. Claude Desktop on macbook)"
          maxLength={64}
        />
        <button
          onClick={handleCreate}
          disabled={isCreating || !label.trim()}
          className="btn-primary whitespace-nowrap"
        >
          {isCreating ? 'Creating...' : 'Generate Token'}
        </button>
      </div>

      {/* Active tokens list */}
      {activeTokens.length > 0 && (
        <div className="space-y-2 mb-4">
          <h3 className="text-xs font-semibold text-text-secondary uppercase">Active Tokens</h3>
          <div className="space-y-1">
            {activeTokens.map((t) => (
              <div
                key={t.id}
                className="flex items-center justify-between py-2 px-3 rounded bg-bg-secondary text-sm"
              >
                <div className="flex-1 min-w-0">
                  <div className="text-text-primary font-medium truncate">{t.label}</div>
                  <div className="text-[10px] text-text-secondary">
                    Created {new Date(t.created_at).toLocaleDateString()}
                    {t.last_used_at
                      ? ` · Last used ${new Date(t.last_used_at).toLocaleDateString()}`
                      : ' · Never used'}
                  </div>
                </div>
                <button
                  onClick={() => handleRevoke(t.id)}
                  className="text-xs text-danger hover:underline ml-3"
                >
                  Revoke
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Show snippets toggle */}
      <button
        onClick={() => setShowSnippets((s) => !s)}
        className="text-xs text-accent hover:underline"
      >
        {showSnippets ? 'Hide' : 'Show'} setup instructions
      </button>

      {showSnippets && (
        <div className="mt-4 space-y-4 text-xs">
          <div>
            <div className="flex items-center justify-between mb-1">
              <h4 className="font-semibold text-text-primary">Claude.ai Web (HTTP / SSE)</h4>
              <button onClick={() => copy(httpUrl)} className="text-accent hover:underline">
                Copy URL
              </button>
            </div>
            <p className="text-text-secondary mb-2">
              In Claude.ai → Settings → Connectors → Add MCP server. Use this URL and paste your
              token as the bearer credential.
            </p>
            <code className="block bg-bg-secondary px-2 py-1.5 rounded font-mono break-all">
              {httpUrl}
            </code>
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <h4 className="font-semibold text-text-primary">Claude Desktop / Code (stdio)</h4>
              <button
                onClick={() => copy(stdioConfig('YOUR_TOKEN_HERE'))}
                className="text-accent hover:underline"
              >
                Copy snippet
              </button>
            </div>
            <p className="text-text-secondary mb-2">
              Add this to <code className="font-mono">claude_desktop_config.json</code>. Replace{' '}
              <code className="font-mono">YOUR_TOKEN_HERE</code> with the token you just generated,
              and update <code className="font-mono">cwd</code> to your local Perpl backend path.
            </p>
            <pre className="bg-bg-secondary px-2 py-1.5 rounded font-mono whitespace-pre overflow-x-auto">
{stdioConfig('YOUR_TOKEN_HERE')}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

function SettingsContent() {
  const { user, _hydrated } = useAuthStore();
  const { login, logout, isLoading: authLoading, address, isAuthenticated, isConnected } = useAuth();
  const toast = useToast();
  const [perplToken, setPerplToken] = useState('');
  const [isLinking, setIsLinking] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [isSettingUsername, setIsSettingUsername] = useState(false);

  const { data: positions, isLoading: positionsLoading } = useQuery({
    queryKey: ['my-positions'],
    queryFn: getMyPositions,
    enabled: isAuthenticated && !!user?.perpl_linked,
  });

  const handleLinkPerpl = async () => {
    if (!perplToken.trim()) {
      toast.error('Please enter your Perpl auth token');
      return;
    }

    setIsLinking(true);
    try {
      await postLinkPerpl(perplToken);
      toast.success('Perpl account linked successfully');
      setPerplToken('');
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to link Perpl account');
    } finally {
      setIsLinking(false);
    }
  };

  const handleSetUsername = async () => {
    const trimmed = newUsername.trim();
    if (!trimmed) {
      toast.error('Please enter a username');
      return;
    }
    if (!/^[a-zA-Z0-9_]{3,30}$/.test(trimmed)) {
      toast.error('Username must be 3-30 characters, letters/numbers/underscores only');
      return;
    }
    setIsSettingUsername(true);
    try {
      await postSetUsername(trimmed);
      toast.success('Username set successfully');
      setNewUsername('');
      // Update local auth store with new username
      const { setAuth, token } = useAuthStore.getState();
      if (token && user) {
        setAuth(token, { ...user, username: trimmed });
      }
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to set username');
    } finally {
      setIsSettingUsername(false);
    }
  };

  // Wait for store hydration before deciding auth state
  if (!_hydrated) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold text-text-primary">Settings</h1>
        <div className="card text-center py-12">
          <div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin mx-auto" />
        </div>
      </div>
    );
  }

  if (!isAuthenticated && !isConnected) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold text-text-primary">Settings</h1>
        <div className="card text-center py-12">
          <svg
            className="w-16 h-16 mx-auto mb-4 text-text-secondary/40"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1}
              d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
            />
          </svg>
          <h3 className="text-lg font-semibold text-text-primary mb-2">
            Connect Your Wallet
          </h3>
          <p className="text-sm text-text-secondary mb-6">
            Connect your wallet to access settings.
          </p>
          <button
            onClick={() => login()}
            disabled={authLoading}
            className="btn-primary"
          >
            {authLoading ? 'Connecting...' : 'Connect Wallet'}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-text-primary">Settings</h1>

      {/* Sign In prompt — only if wagmi connected but no SIWE token */}
      {isConnected && !isAuthenticated && !authLoading && (
        <div className="card border-accent/20">
          <p className="text-sm text-text-secondary mb-3">Wallet connected. Sign to access all features.</p>
          <button onClick={() => login()} className="btn-primary text-sm">Sign In</button>
        </div>
      )}

      {/* User Info */}
      <div className="card">
        <h2 className="text-lg font-semibold text-text-primary mb-4">
          Account
        </h2>
        <div className="space-y-3">
          <div className="flex items-center justify-between py-2 border-b border-text-secondary/10">
            <span className="text-sm text-text-secondary">Wallet Address</span>
            <span className="text-sm text-text-primary font-mono">
              {address ? shortenAddress(address) : 'N/A'}
            </span>
          </div>
          <div className="py-2 border-b border-text-secondary/10">
            <div className="flex items-center justify-between">
              <span className="text-sm text-text-secondary">Username</span>
              {user?.username ? (
                <span className="text-sm text-text-primary font-semibold">{user.username}</span>
              ) : (
                <span className="text-xs text-text-secondary/60">Not set</span>
              )}
            </div>
            {!user?.username && (
              <div className="flex items-center gap-2 mt-2">
                <input
                  type="text"
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                  className="input-field flex-1 text-sm"
                  placeholder="Choose a username..."
                  maxLength={30}
                />
                <button
                  onClick={handleSetUsername}
                  disabled={isSettingUsername || !newUsername.trim()}
                  className="btn-primary text-xs whitespace-nowrap"
                >
                  {isSettingUsername ? 'Setting...' : 'Set Username'}
                </button>
              </div>
            )}
            {!user?.username && (
              <p className="text-[10px] text-text-secondary/60 mt-1">3-30 chars, letters/numbers/underscores. Cannot be changed later.</p>
            )}
          </div>
          <div className="flex items-center justify-between py-2 border-b border-text-secondary/10">
            <span className="text-sm text-text-secondary">Perpl Account</span>
            <span
              className={`text-sm font-medium ${
                user?.perpl_linked ? 'text-success' : 'text-warning'
              }`}
            >
              {user?.perpl_linked ? 'Linked' : 'Not Linked'}
            </span>
          </div>
          <div className="pt-2">
            <button onClick={logout} className="btn-danger text-sm">
              Disconnect Wallet
            </button>
          </div>
        </div>
      </div>

      {/* Link Perpl */}
      {!user?.perpl_linked && (
        <div className="card">
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            Link Perpl Account
          </h2>
          <p className="text-sm text-text-secondary mb-4">
            Enter your Perpl authentication token to link your trading account.
            This is required for copy trading.
          </p>
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 sm:gap-3">
            <input
              type="text"
              value={perplToken}
              onChange={(e) => setPerplToken(e.target.value)}
              className="input-field flex-1"
              placeholder="Enter Perpl auth token..."
            />
            <button
              onClick={handleLinkPerpl}
              disabled={isLinking || !perplToken.trim()}
              className="btn-primary whitespace-nowrap"
            >
              {isLinking ? 'Linking...' : 'Link Account'}
            </button>
          </div>
        </div>
      )}

      {/* One-Click Trading (Perpl API key) */}
      <OneClickTradingCard />

      {/* Telegram Notifications */}
      <TelegramCard />

      {/* MCP Access (Claude integrations) */}
      <McpCard />

      {/* My Positions */}
      {user?.perpl_linked && (
        <div className="card">
          <h2 className="text-lg font-semibold text-text-primary mb-4">
            My Positions
          </h2>
          {positionsLoading ? (
            <div className="flex justify-center py-8">
              <LoadingSpinner />
            </div>
          ) : !positions || positions.length === 0 ? (
            <div className="text-sm text-text-secondary text-center py-8">
              No open positions
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {positions.map((pos: any, idx: number) => (
                <PositionCard key={idx} position={pos} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Settings() {
  return (
    <ErrorBoundary>
      <SettingsContent />
    </ErrorBoundary>
  );
}

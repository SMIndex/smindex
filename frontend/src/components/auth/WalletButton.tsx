import { useState, useRef, useEffect } from 'react';
import { useAccount, useConnect } from 'wagmi';
import { useAuth } from '@/hooks/useAuth';
import { useAuthStore } from '@/stores/authStore';
import { shortenAddress } from '@/lib/formatters';
import { useToast } from '@/components/common/Toast';

export default function WalletButton() {
  const toast = useToast();
  const { login, logout, isLoading, isAuthenticated, address, user } = useAuth();
  const { isConnected: wagmiConnected, address: wagmiAddress } = useAccount();
  const hydrated = useAuthStore((s) => s._hydrated);
  const { connectors } = useConnect();
  const [showDropdown, setShowDropdown] = useState(false);
  const [showWalletPicker, setShowWalletPicker] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const authAttemptedRef = useRef(false);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
        setShowWalletPicker(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Auto-switch chain + sign when wallet connects
  useEffect(() => {
    if (wagmiConnected && wagmiAddress && !isAuthenticated && !isLoading && !authAttemptedRef.current) {
      authAttemptedRef.current = true;
      // Switch chain first, then auth
      (async () => {
        try {
          const { switchChain } = await import('wagmi/actions');
          const { wagmiConfig, monad } = await import('@/config/wagmi');
          await switchChain(wagmiConfig, { chainId: monad.id });
        } catch {}
        login().catch(() => {});
      })();
    }
    if (!wagmiConnected) authAttemptedRef.current = false;
  }, [wagmiConnected, wagmiAddress, isAuthenticated, isLoading]);

  const displayAddress = address || wagmiAddress;
  const isWalletConnected = isAuthenticated || (wagmiConnected && !!wagmiAddress);

  if (isLoading) {
    return (
      <button disabled className="btn-primary flex items-center gap-2 text-xs md:text-sm px-2.5 md:px-4 py-1.5 opacity-50">
        <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
        <span className="hidden md:inline">Signing...</span>
      </button>
    );
  }

  // Don't show "Connect" until store hydrates from localStorage
  if (!hydrated) {
    return <div className="w-20 h-8 rounded-lg bg-bg-card animate-pulse" />;
  }

  if (!isWalletConnected || !displayAddress) {
    return (
      <div className="relative" ref={dropdownRef}>
        <button
          onClick={() => setShowWalletPicker(!showWalletPicker)}
          className="btn-primary text-xs md:text-sm px-2.5 md:px-4 py-1.5"
        >
          <span className="hidden md:inline">Connect Wallet</span>
          <span className="md:hidden">Connect</span>
        </button>

        {showWalletPicker && (
          <div className="absolute right-0 mt-2 w-56 bg-bg-card border border-text-secondary/20 rounded-xl shadow-xl overflow-hidden z-50 animate-fadeIn">
            <div className="px-3 py-2 border-b border-text-secondary/10 text-xs text-text-secondary font-medium">
              Connect Wallet
            </div>
            {connectors.map((connector) => (
              <button
                key={connector.uid}
                onClick={async () => {
                  setShowWalletPicker(false);
                  try {
                    const { connect, switchChain } = await import('wagmi/actions');
                    const { wagmiConfig, monad } = await import('@/config/wagmi');
                    await connect(wagmiConfig, { connector });
                    // Switch to Monad after connecting
                    try { await switchChain(wagmiConfig, { chainId: monad.id }); } catch {}
                  } catch (err: any) {
                    toast.error(err?.shortMessage || err?.message || 'Failed to connect wallet');
                  }
                }}
                className="w-full px-3 py-3 text-left text-sm text-text-primary hover:bg-bg-secondary transition-colors flex items-center gap-3"
              >
                <div className="w-8 h-8 rounded-lg bg-accent/10 flex items-center justify-center shrink-0">
                  <span className="text-accent text-xs font-bold">
                    {connector.name === 'WalletConnect' ? 'WC' : connector.name[0]}
                  </span>
                </div>
                <div>
                  <div className="font-medium">{connector.name}</div>
                  <div className="text-[10px] text-text-secondary">
                    {connector.name === 'WalletConnect' ? 'Mobile & QR code' : 'Browser extension'}
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setShowDropdown(!showDropdown)}
        className="flex items-center gap-1.5 md:gap-2 px-2 md:px-3 py-1.5 bg-bg-card border border-text-secondary/20 rounded-lg text-xs md:text-sm text-text-primary hover:border-accent/40 transition-colors"
      >
        <div className="w-2 h-2 rounded-full bg-success shrink-0" />
        <span className="hidden md:inline">{shortenAddress(displayAddress)}</span>
        <span className="md:hidden">{displayAddress.slice(0, 4)}...{displayAddress.slice(-3)}</span>
        <svg className="w-3 h-3 text-text-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {showDropdown && (
        <div className="absolute right-0 mt-2 w-48 bg-bg-card border border-text-secondary/20 rounded-lg shadow-xl overflow-hidden z-50">
          <div className="px-3 py-2 border-b border-text-secondary/10">
            <div className="text-xs text-text-secondary">Connected</div>
            <div className="text-sm text-text-primary font-mono">{shortenAddress(displayAddress)}</div>
          </div>
          {!isAuthenticated && wagmiConnected && (
            <button
              onClick={() => { login(); setShowDropdown(false); }}
              className="w-full px-3 py-2 text-left text-sm text-accent hover:bg-accent/10 transition-colors"
            >
              Sign In
            </button>
          )}
          <button
            onClick={() => { logout(); setShowDropdown(false); }}
            className="w-full px-3 py-2 text-left text-sm text-danger hover:bg-danger/10 transition-colors"
          >
            Disconnect
          </button>
        </div>
      )}
    </div>
  );
}

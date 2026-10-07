import { useCallback, useState, useEffect } from 'react';
import { useConnect, useDisconnect, useAccount, useSignMessage } from 'wagmi';
import { useAuthStore } from '@/stores/authStore';
import { postAuthPayload, postAuthConnect } from '@/lib/api';

export function useAuth() {
  const { address, isConnected } = useAccount();
  const { connectAsync, connectors } = useConnect();
  const { disconnectAsync } = useDisconnect();
  const { signMessageAsync } = useSignMessage();
  const { isAuthenticated, user, token, _hydrated, setAuth, clearAuth } = useAuthStore();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // On mobile PWA, wagmi may lose connection state but our auth token
  // (stored in localStorage) is still valid. Use stored auth as source of truth.
  const effectiveAddress = address || user?.wallet_address || null;
  const effectiveAuth = isAuthenticated && !!token;

  // Clear auth store when API returns 401 (token expired/invalid)
  useEffect(() => {
    const handler = () => clearAuth();
    window.addEventListener('auth_expired', handler);
    return () => window.removeEventListener('auth_expired', handler);
  }, [clearAuth]);

  const doSiweAuth = async (walletAddress: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const payload = await postAuthPayload(walletAddress);

      const message = [
        `${payload.domain} wants you to sign in with your Ethereum account:`,
        payload.address,
        '',
        payload.statement,
        '',
        `URI: ${payload.uri}`,
        `Version: ${payload.version}`,
        `Chain ID: ${payload.chain_id}`,
        `Nonce: ${payload.nonce}`,
        `Issued At: ${payload.issued_at}`,
      ].join('\n');

      const signature = await signMessageAsync({ message });
      const { token: newToken, user: userData } = await postAuthConnect(message, signature);
      setAuth(newToken, userData);
    } catch (err: any) {
      const msg = err?.shortMessage || err?.message || 'Authentication failed';
      setError(msg);
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  const login = useCallback(async () => {
    // If already authenticated via stored token, don't re-auth
    if (effectiveAuth) return;

    setIsLoading(true);
    setError(null);
    try {
      if (!isConnected) {
        // Try injected first (desktop extensions)
        const injectedConnector = connectors.find(
          (c) => c.id === 'injected' || c.name === 'MetaMask',
        );

        if (injectedConnector && typeof window !== 'undefined' && (window as any).ethereum) {
          const result = await connectAsync({ connector: injectedConnector });
          const walletAddress = result.accounts[0];
          if (walletAddress) {
            await doSiweAuth(walletAddress);
            return;
          }
        }

        // No injected wallet — WalletButton handles connector picker
        setIsLoading(false);
        return;
      }

      // Already connected via wagmi, just do SIWE
      if (address) {
        await doSiweAuth(address);
      }
    } catch (err: any) {
      const msg = err?.shortMessage || err?.message || 'Authentication failed';
      setError(msg);
      setIsLoading(false);
    }
  }, [address, isConnected, connectors, connectAsync, effectiveAuth]);

  const logout = useCallback(async () => {
    clearAuth();
    try {
      await disconnectAsync();
    } catch {
      // ignore
    }
  }, [clearAuth, disconnectAsync]);

  return {
    login,
    logout,
    isLoading,
    error,
    hydrated: _hydrated,
    // Use stored auth as truth — survives PWA background/foreground cycles
    isAuthenticated: effectiveAuth,
    isConnected: isConnected || effectiveAuth,
    address: effectiveAddress,
    user,
  };
}

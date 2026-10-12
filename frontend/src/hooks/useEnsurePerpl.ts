import { useCallback, useRef } from 'react';
import { useSignMessage } from 'wagmi';

import { useAuth } from '@/hooks/useAuth';
import { useToast } from '@/components/common/Toast';
import {
  connectPerpl, getPerplPayload, getSession, openTradingWs, setAccountId, tryAutoReconnect,
} from '@/lib/perplTrading';
import { getAccountState, refreshAccount } from '@/lib/accountStore';

/**
 * One way to get a Perpl trading session (2026-10-08).
 *
 * Order of attempts, and this is the whole of it:
 *   1. already connected -> return;
 *   2. the enrolled one-click key (Ed25519, mt:29) -> NO wallet popup. This is
 *      the normal path once one-click is on, and it is also why reading never
 *      needs a signature: reads go through our backend with the app session,
 *      and the venue socket signs itself with the key;
 *   3. only if there is no usable key: one SIWE signature.
 *
 * A failed attempt shows ONE toast and returns false. Concurrent callers share
 * the same attempt, so two components cannot raise two wallet prompts.
 */
export function useEnsurePerpl(): () => Promise<boolean> {
  const { address } = useAuth();
  const { signMessageAsync } = useSignMessage();
  const toast = useToast();
  const inFlight = useRef<Promise<boolean> | null>(null);

  return useCallback(async (): Promise<boolean> => {
    const sess = getSession();
    if (sess.authenticated && sess.hasWs) return true;
    if (!address) {
      toast.error('Connect your wallet first');
      return false;
    }
    if (inFlight.current) return inFlight.current;

    inFlight.current = (async () => {
      try {
        // No popup: one-click key, or a still-valid stored session.
        if (await tryAutoReconnect(address)) return true;
        const payload = await getPerplPayload(address);
        const signature = await signMessageAsync({ message: payload.message });
        const nonce = await connectPerpl(address, payload, signature);
        await openTradingWs(nonce, address);
        const accountId = getAccountState().accountId;
        if (accountId) setAccountId(accountId);
        else await refreshAccount('after-perpl-connect');
        return true;
      } catch (e: any) {
        toast.error(e?.shortMessage || e?.message || 'Could not connect to Perpl');
        return false;
      } finally {
        inFlight.current = null;
      }
    })();
    return inFlight.current;
  }, [address, signMessageAsync, toast]);
}

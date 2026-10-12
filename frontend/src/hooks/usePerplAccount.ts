import { useEffect, useState } from 'react';

import {
  getAccountState, initAccountStore, refreshAccount, subscribeAccount, type AccountState,
} from '@/lib/accountStore';

/**
 * The shared account store as a hook. Every screen that shows positions,
 * orders, balance or margin uses this, there is exactly one chain poll and one
 * venue subscription for the whole app, so two screens cannot disagree.
 */
export function usePerplAccount(wallet?: string | null): AccountState & { refresh: (why?: string) => void } {
  const [state, setState] = useState<AccountState>(getAccountState());

  useEffect(() => {
    initAccountStore(wallet);
    const unsub = subscribeAccount(setState);
    setState(getAccountState());
    return unsub;
  }, [wallet]);

  return { ...state, refresh: refreshAccount };
}

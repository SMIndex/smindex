import { createConfig, http } from 'wagmi';
import { injected } from 'wagmi/connectors';
import { walletConnect } from 'wagmi/connectors';
import { defineChain } from 'viem';

export const monad = defineChain({
  id: 143,
  name: 'Monad',
  nativeCurrency: {
    name: 'MON',
    symbol: 'MON',
    decimals: 18,
  },
  rpcUrls: {
    default: {
      http: ['https://rpc.monad.xyz'],
    },
  },
});

const projectId = import.meta.env.VITE_WALLETCONNECT_ID || '53a48e6431d70ede623ba2bf74ef9b97';

// Use injected + WalletConnect connectors directly (no AppKit overhead)
export const wagmiConfig = createConfig({
  chains: [monad],
  connectors: [
    injected(),
    walletConnect({
      projectId,
      metadata: {
        name: 'SMINDEX',
        description: 'The smart money index for onchain perps',
        url: 'https://smindex.xyz',
        icons: ['https://smindex.xyz/icons/icon-512.png'],
      },
      showQrModal: true,
    }),
  ],
  transports: {
    [monad.id]: http(),
  },
});

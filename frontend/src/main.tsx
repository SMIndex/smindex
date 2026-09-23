import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { WagmiProvider } from 'wagmi';
import { reconnect } from '@wagmi/core';
import { wagmiConfig } from '@/config/wagmi';
import App from '@/App';
import { loadMarketConfigs } from '@/lib/perplTrading';
import '@/index.css';

// Stale-chunk recovery (Part B bug 2, proven by nginx: an open tab requested
// /assets/actions-B32gJKNu.js from a replaced bundle -> 404 -> the failed
// dynamic import rendered as a dead/not-found page). On a preload failure,
// reload ONCE to pick up the fresh index + chunk map; the sessionStorage
// guard prevents a reload loop if the failure is not deploy-related.
window.addEventListener('vite:preloadError', (e) => {
  if (sessionStorage.getItem('chunk-reloaded') !== '1') {
    sessionStorage.setItem('chunk-reloaded', '1');
    e.preventDefault();
    window.location.reload();
  }
});
window.addEventListener('load', () => sessionStorage.removeItem('chunk-reloaded'));

// Load Perpl market configs before rendering
loadMarketConfigs();

// Reconnect wagmi on page load (restores WalletConnect session from localStorage)
reconnect(wagmiConfig).catch(() => {});

// Also reconnect when PWA comes back from background
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    reconnect(wagmiConfig).catch(() => {});
  }
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <WagmiProvider config={wagmiConfig} reconnectOnMount>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </WagmiProvider>
  </React.StrictMode>,
);

// Register service worker for PWA
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

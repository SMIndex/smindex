import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Stable vendor chunks: they download in parallel over HTTP/2 and keep
        // their hash (and browser cache) across app-only deploys. Wallet libs
        // (viem/wagmi) are deliberately NOT forced into one chunk: that pulled
        // lazy-only modules (@noble, ox) into the first-load set (+70 KB).
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          if (/[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/.test(id)) return 'vendor-react';
          if (/[\\/]node_modules[\\/]@tanstack[\\/]/.test(id)) return 'vendor-query';
          if (/[\\/]node_modules[\\/]lightweight-charts[\\/]/.test(id)) return 'vendor-charts';
          return undefined;
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/ws/feed': {
        target: 'http://localhost:8001',
        ws: true,
        changeOrigin: true,
      },
      '/ws/trading': {
        target: 'http://localhost:8001',
        ws: true,
        changeOrigin: true,
      },
      '/ws/market-data': {
        target: 'http://localhost:8001',
        ws: true,
        changeOrigin: true,
      },
    },
  },
});

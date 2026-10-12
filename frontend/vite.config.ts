import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { execSync } from 'child_process';
import path from 'path';

// Build stamp (2026-10-09): the owner could not tell whether the page he was
// looking at was the new build. Commit + build time are compiled in and shown
// in the footer of every page, and on window.__SMINDEX_BUILD__ so a headless
// check (and the deploy script) can assert which build a browser actually ran.
function gitCommit(): string {
  try {
    return execSync('git rev-parse --short HEAD', { encoding: 'utf8' }).trim();
  } catch {
    return 'nogit';
  }
}

// Third-party libraries ship long dashes inside a few error messages. The site
// carries none of its own, so the built files are kept free of them as well.
const LONG_DASHES = new RegExp('[' + String.fromCharCode(8212, 8211) + ']', 'g');
function plainDashes() {
  return {
    name: 'plain-dashes',
    generateBundle(_options: unknown, bundle: Record<string, any>) {
      for (const file of Object.values(bundle)) {
        if (file.type === 'chunk') file.code = file.code.replace(LONG_DASHES, '-');
        else if (typeof file.source === 'string') file.source = file.source.replace(LONG_DASHES, '-');
      }
    },
  };
}

export default defineConfig({
  define: {
    __BUILD_COMMIT__: JSON.stringify(gitCommit()),
    __BUILD_TIME__: JSON.stringify(new Date().toISOString()),
  },
  plugins: [react(), plainDashes()],
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

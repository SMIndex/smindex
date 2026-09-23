import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
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

const CACHE_NAME = 'smindex-v10';

// Install: skip waiting to activate immediately
self.addEventListener('install', () => {
  self.skipWaiting();
});

// Activate: claim all clients, clean old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch handler
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET, WebSocket, and chrome-extension requests
  if (request.method !== 'GET') return;
  if (url.protocol === 'ws:' || url.protocol === 'wss:') return;
  if (url.protocol === 'chrome-extension:') return;

  // API and WS calls: network only (don't cache API responses)
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws/')) {
    return;
  }

  // Navigation requests (HTML pages): always serve index.html for SPA routing
  // Also catch same-origin paths without file extensions (SPA routes like /settings)
  // which may arrive as mode=cors/same-origin in PWA or preload contexts
  const isSpaRoute =
    request.mode === 'navigate' ||
    (url.origin === self.location.origin && !url.pathname.includes('.') && !url.pathname.startsWith('/api/'));
  if (isSpaRoute) {
    // cache: 'no-cache' forces revalidation with the server so a deploy is
    // picked up immediately instead of serving a heuristically-cached copy
    event.respondWith(
      fetch('/index.html', { cache: 'no-cache' }).catch(() => caches.match('/index.html'))
    );
    return;
  }

  // Static assets (JS/CSS/images): network-first with cache fallback
  // This ensures new deploys get fresh chunks while still working offline
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok && url.origin === self.location.origin) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
        }
        return response;
      })
      .catch(() => caches.match(request))
  );
});

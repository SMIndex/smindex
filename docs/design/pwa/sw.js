/* Perpl Terminal service worker: app shell cached, data always from network */
const SHELL = 'perpl-shell-v1';
const ASSETS = ['./', './index.html', './manifest.webmanifest', './icons/icon-192.png', './icons/icon-512.png'];
self.addEventListener('install', e => { e.waitUntil(caches.open(SHELL).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== SHELL).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  // never cache API, websocket upgrade or third party data
  if (url.pathname.startsWith('/api/') || url.hostname !== location.hostname) return;
  // shell: cache first, then network, and refresh the cache in the background
  e.respondWith(caches.match(e.request).then(hit => {
    const fetchP = fetch(e.request).then(res => { if (res.ok) caches.open(SHELL).then(c => c.put(e.request, res.clone())); return res; }).catch(() => hit);
    return hit || fetchP;
  }));
});

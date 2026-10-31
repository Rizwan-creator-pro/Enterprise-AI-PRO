// Enterprise AI Hub - Service Worker for PWA
const CACHE_NAME = 'enterprise-ai-cache-v2';
const ASSETS_TO_CACHE = [
  '/',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon.svg',
  '/static/js/export_common.js',
  '/static/js/export_pdf.js',
  '/static/js/export_excel.js',
  '/static/js/export_word.js',
  '/static/js/export_ppt.js'
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE).catch((err) => {
        console.warn('[SW] Cache addAll non-critical error:', err);
      });
    })
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // API calls: Network-only (no caching of chat or auth)
  if (url.pathname.startsWith('/api/') || event.request.method !== 'GET') {
    return;
  }

  // Static shell & assets: Stale-while-revalidate or Network-first
  event.respondWith(
    fetch(event.request)
      .then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic') {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache);
          });
        }
        return networkResponse;
      })
      .catch(() => caches.match(event.request))
  );
});

// Enterprise AI Hub - Service Worker for PWA
const CACHE_NAME = 'enterprise-ai-cache-v5';
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
  let url;
  try {
    url = new URL(event.request.url);
  } catch (e) {
    return;
  }

  // Only handle standard HTTP/HTTPS GET requests. Skip API calls and non-http schemes (e.g. chrome-extension://)
  if (!url.protocol.startsWith('http') || event.request.method !== 'GET' || url.pathname.startsWith('/api/')) {
    return;
  }

  // Static shell & assets: Stale-while-revalidate or Network-first
  event.respondWith(
    fetch(event.request)
      .then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic') {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache).catch(() => {});
          }).catch(() => {});
        }
        return networkResponse;
      })
      .catch(() => caches.match(event.request))
  );
});

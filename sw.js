/* =====================================================
   Service Worker — 勤怠カレンダー PWA  (GAFA-grade)
   ※ CACHE バージョンを上げると全クライアントのキャッシュが更新される
   ===================================================== */

const CACHE      = 'kintai-v19';
const CACHE_STATIC = 'kintai-static-v1'; // アイコン等の静的アセット
const PRECACHE   = ['./index.html', './manifest.json', './recover.html', './icon-apple.png', './icon-192.png', './icon-512.png'];
const STATIC_EXT = ['.png', '.jpg', '.svg', '.ico', '.woff2'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k !== CACHE && k !== CACHE_STATIC)
          .map(k => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// Fetch 戦略:
//   静的アセット(.png等) → Stale-While-Revalidate（高速表示 + バックグラウンド更新）
//   HTML / JS / JSON     → Network-First（常に最新コードを使用）
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  const isStatic = STATIC_EXT.some(ext => url.pathname.endsWith(ext));

  if (isStatic) {
    // Stale-While-Revalidate
    e.respondWith(
      caches.open(CACHE_STATIC).then(async cache => {
        const cached = await cache.match(e.request);
        const fetchPromise = fetch(e.request).then(res => {
          if (res.ok) cache.put(e.request, res.clone());
          return res;
        }).catch(() => null);
        return cached || fetchPromise;
      })
    );
  } else {
    // Network-First
    e.respondWith(
      fetch(e.request).then(res => {
        if (res.ok) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(() =>
        caches.match(e.request).then(cached => cached || caches.match('./index.html'))
      )
    );
  }
});

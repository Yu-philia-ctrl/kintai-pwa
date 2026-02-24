/* =====================================================
   Service Worker — 勤怠カレンダー PWA
   ※ CACHE バージョンを上げると全クライアントのキャッシュが更新される
   ===================================================== */

const CACHE = 'kintai-v4';
const PRECACHE = ['./index.html', './manifest.json', './recover.html', './icon-apple.png', './icon-192.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ネットワーク優先（更新があれば即反映）、失敗時のみキャッシュ
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request).then(res => {
      // 正常レスポンスをキャッシュに上書き保存
      const clone = res.clone();
      caches.open(CACHE).then(c => c.put(e.request, clone));
      return res;
    }).catch(() =>
      // オフライン時のみキャッシュから返す
      caches.match(e.request).then(cached => cached || caches.match('./index.html'))
    )
  );
});

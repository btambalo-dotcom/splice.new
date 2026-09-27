/* SPLICER service worker — etapa 1
   - Guarda só arquivos estáticos (ícones, CSS, JS) para abrir rápido.
   - Páginas do sistema sempre vêm da internet (dados sempre atualizados).
   - Sem sinal: mostra a página /static/offline.html. */
const CACHE = 'splicer-static-v1';
const PRECACHE = [
  '/static/offline.html',
  '/static/icons/icon-192.png',
  '/static/icons/apple-touch-icon.png',
  '/static/img/logo.png',
  '/static/css/app.css',
  '/static/style.css',
  '/static/js/lang.js'
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;               // envios (POST) nunca passam pelo cache
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // Páginas: sempre da internet; sem sinal -> página offline
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => caches.match('/static/offline.html')));
    return;
  }

  // Arquivos estáticos (menos fotos enviadas): cache primeiro, atualiza em segundo plano
  if (url.pathname.startsWith('/static/') && !url.pathname.startsWith('/static/uploads/')) {
    e.respondWith(
      caches.open(CACHE).then(async (cache) => {
        const hit = await cache.match(req);
        const net = fetch(req).then((res) => { if (res.ok) cache.put(req, res.clone()); return res; }).catch(() => hit);
        return hit || net;
      })
    );
  }
});

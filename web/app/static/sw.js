// Service worker mínimo: cachea el shell estático y da una página de reserva cuando no hay red.
// No cachea HTML dinámico (listados, detalle) porque cambia todo el tiempo y es por-usuario.
const CACHE = 'inmo-shell-v5';
const SHELL = [
  '/static/app.css', '/static/app.js', '/static/htmx.min.js',
  '/static/offline.html', '/static/manifest.json',
  '/static/icons/icon-192.png', '/static/icons/icon-512.png', '/static/icons/icon.svg',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const { request } = e;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) return;

  if (request.mode === 'navigate') {
    e.respondWith(fetch(request).catch(() => caches.match('/static/offline.html')));
    return;
  }
  if (url.pathname.startsWith('/static/')) {
    // Stale-while-revalidate: responde con la copia en caché (rápido, sirve sin red) y la actualiza en segundo
    // plano, así los cambios de CSS/JS llegan en la visita siguiente sin tener que cambiar el nombre del caché.
    e.respondWith(caches.open(CACHE).then((c) => c.match(request).then((cached) => {
      const red = fetch(request).then((res) => {
        if (res.ok) c.put(request, res.clone());
        return res;
      });
      if (cached) { e.waitUntil(red.catch(() => {})); return cached; }
      return red;
    })));
  }
});

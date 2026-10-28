/* AISP app shell (10-27). Live data is never cached: /api and /preview-api always go to the network.
   Hashed build assets are cache-first; pages are network-first with the last shell as the offline fallback. */
const VERSION = 'aisp-shell-v1'
const SHELL = ['/', '/manifest.webmanifest', '/icons/aisp-192.png', '/icons/aisp-512.png']

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).catch(() => undefined).then(() => self.skipWaiting()))
})
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()))
})
self.addEventListener('fetch', (e) => {
  const req = e.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/preview-api/')) return
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(VERSION).then((c) => c.put('/', copy)) }
      return res
    }).catch(() => caches.match('/').then((r) => r || new Response('<h1>AISP is offline</h1><p>Reconnect to see tonight’s games.</p>', { headers: { 'content-type': 'text/html' } }))))
    return
  }
  if (url.pathname.startsWith('/assets/') || url.pathname.startsWith('/icons/')) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)) }
      return res
    })))
  }
})
self.addEventListener('notificationclick', (e) => {
  e.notification.close()
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    const open = list.find((c) => 'focus' in c)
    return open ? open.focus() : self.clients.openWindow('/?view=focus&source=alert')
  }))
})

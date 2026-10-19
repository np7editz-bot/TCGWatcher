// TCGWatcher service worker: the shell is cached (instant open, works offline); data is network-first with the last
// good copy as fallback, so the app always shows something and the page's own "Prijzen van …" line says how old it is.
const VERSION = "v1";
const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icons/icon-192.png", "./icons/icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open("shell-" + VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => !k.endsWith(VERSION)).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;       // card images, shops: straight through
  if (url.pathname.includes("/data/")) {                                            // latest.json: fresh if possible
    e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open("data-" + VERSION).then(c => c.put(e.request, copy)); return r; })
      .catch(() => caches.match(e.request)));
    return;
  }
  e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request).then(r => {   // shell: cache, refresh in background
    const copy = r.clone(); caches.open("shell-" + VERSION).then(c => c.put(e.request, copy)); return r; })));
});

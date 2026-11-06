// TCGWatcher service worker: everything is network-first with the last good copy as fallback (works offline, never
// stale). v1 served the shell cache-first and never refreshed it, so an installed app kept running the index.html from
// the day it was installed while the data moved on — the slabs tab "lost" cards that newer page code would show.
const VERSION = "v2";   // bump → old shell caches are dropped on activate
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
  e.respondWith(fetch(e.request).then(r => {                                        // shell: network-first, cache as fallback
    const copy = r.clone(); caches.open("shell-" + VERSION).then(c => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request)));
});

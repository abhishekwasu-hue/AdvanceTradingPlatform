/* AiAlgoDesk service worker.
 * Phase O3: Web Push - receives encrypted pushes from the platform (RFC 8291 payloads are decrypted by the browser
 * before this runs) and shows them as system notifications; clicking opens the page the push names.
 * PW1: the app-shell cache - static files only. The build (vite.config.ts, atpServiceWorker) replaces the version
 * placeholder below with the build id, so every deploy is a new worker and the page can offer "Reload".
 * Never cached: API responses, market data, anything cross-origin, anything that is not a GET. A page opened offline
 * gets offline.html, never a stale copy of a screen with prices on it. */
const VERSION = "__ATP_BUILD__";
const SHELL_CACHE = `atp-shell-${VERSION}`;
const PRECACHE = ["/offline.html", "/favicon.svg", "/icons/icon-192.png", "/manifest.webmanifest"];

/** What the worker may store: hashed build files (they never change under the same name) and the precache list. */
function isShellAsset(url) {
  if (url.origin !== self.location.origin) return false;
  if (url.pathname.startsWith("/api/")) return false;
  return url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/") || PRECACHE.includes(url.pathname);
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(PRECACHE)));
  // No skipWaiting here: a new version waits until the page says "Reload" (or every tab closes), so a running
  // screen never switches code under the user's hands.
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("atp-shell-") && k !== SHELL_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;   // the network, untouched

  if (request.mode === "navigate") {
    // Pages always come from the network (index.html is revalidated on every visit); offline -> the offline page.
    event.respondWith(fetch(request).catch(() => caches.match("/offline.html")));
    return;
  }
  if (!isShellAsset(url)) return;
  event.respondWith(
    caches.match(request).then((hit) => hit || fetch(request).then((response) => {
      if (response.ok && response.type === "basic") {
        const copy = response.clone();
        event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy)));
      }
      return response;
    })),
  );
});

self.addEventListener("push", (event) => {
  let data = { title: "AiAlgoDesk", body: "New alert", url: "/" };
  try { data = { ...data, ...event.data.json() }; } catch (_) { /* plain-text fallback */ if (event.data) data.body = event.data.text(); }
  const options = {
    body: data.body,
    tag: data.notification_id ? `atp-${data.notification_id}` : undefined,
    data: { url: data.url || "/" },
    requireInteraction: data.severity === "CRITICAL" || data.severity === "EMERGENCY",
    icon: "/icons/icon-192.png",
    badge: "/icons/maskable-192.png",
  };
  event.waitUntil(self.registration.showNotification(data.title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
    for (const client of clients) {
      if ("focus" in client) { client.navigate(url); return client.focus(); }
    }
    return self.clients.openWindow(url);
  }));
});

/* Advance Trading Platform - Web Push service worker (Phase O3).
 * Receives encrypted pushes from the platform (RFC 8291 payloads are decrypted by the browser
 * before this runs) and shows them as system notifications; clicking opens the Notifications tab. */
self.addEventListener("push", (event) => {
  let data = { title: "Advance Trading Platform", body: "New alert", url: "/" };
  try { data = { ...data, ...event.data.json() }; } catch (_) { /* plain-text fallback */ if (event.data) data.body = event.data.text(); }
  const options = {
    body: data.body,
    tag: data.notification_id ? `atp-${data.notification_id}` : undefined,
    data: { url: data.url || "/" },
    requireInteraction: data.severity === "CRITICAL" || data.severity === "EMERGENCY",
    icon: "/favicon.svg",
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

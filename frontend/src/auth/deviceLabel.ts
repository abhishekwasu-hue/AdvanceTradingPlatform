/**
 * A4: a device in the session list in plain words ("Chrome on Android", "Safari on iPhone", "The AiAlgoDesk app")
 * instead of the raw browser string. Unknown agents read "A browser"; nothing here is sent anywhere.
 */
export function deviceLabel(userAgent: string | null | undefined): string {
  const ua = userAgent ?? "";
  if (!ua) return "A browser";
  if (/AiAlgoDesk|Capacitor/i.test(ua)) return "The AiAlgoDesk app";
  const os = /iPhone/.test(ua) ? "iPhone"
    : /iPad/.test(ua) ? "iPad"
      : /Android/.test(ua) ? "Android"
        : /Windows/.test(ua) ? "Windows"
          : /Mac OS X|Macintosh/.test(ua) ? "Mac"
            : /CrOS/.test(ua) ? "Chromebook"
              : /Linux/.test(ua) ? "Linux" : "";
  const browser = /Edg\//.test(ua) ? "Edge"
    : /OPR\/|Opera/.test(ua) ? "Opera"
      : /SamsungBrowser/.test(ua) ? "Samsung Internet"
        : /Firefox\/|FxiOS/.test(ua) ? "Firefox"
          : /Chrome\/|CriOS/.test(ua) ? "Chrome"
            : /Safari\//.test(ua) ? "Safari" : "A browser";
  return os ? `${browser} on ${os}` : browser;
}

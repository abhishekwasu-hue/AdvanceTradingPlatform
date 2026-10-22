import { Check, Send } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { ChipV2, LINK_PRIMARY, LINK_SECONDARY } from "../dashboard/CardV2";
import { ActionV2, Note } from "../setup/StepsV2";
import { POLL_MS, TELEGRAM_TEXT, WAIT_MAX_MS, telegramView, type TelegramConnectState } from "./telegramConnect";

/**
 * UX2 fix 5: the one-tap Telegram row (Settings → Alerts and the Setup wizard's alerts step). "Connect Telegram" makes a
 * one-time link to the platform's bot and opens it; the trader presses Start in Telegram and this row turns into
 * "Connected" with "Send a test" by itself. No bot token, chat id or "floor" here: those are under Settings → Advanced.
 */

function problem(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return e.message;
    if (e.status === 403) return "Your role cannot change this. Ask your organisation's owner.";
    if (e.status === 409 || e.status === 503) return e.detail || fallback;   // the server's own plain sentence
  }
  return fallback;
}

export default function TelegramConnectV2({ onChanged, title = true }: { onChanged?: () => void; title?: boolean }) {
  const [state, setState] = useState<TelegramConnectState | null>(null);
  const [failed, setFailed] = useState(false);
  const [link, setLink] = useState<{ url: string; until: number } | null>(null);
  const [busy, setBusy] = useState<"connect" | "test" | "disconnect" | null>(null);
  const [note, setNote] = useState<{ tone: "ok" | "problem"; text: string } | null>(null);
  const [confirmOff, setConfirmOff] = useState(false);
  const changed = useRef(onChanged);
  changed.current = onChanged;

  const load = useCallback(async () => {
    try {
      const next = await api.telegramConnectState();
      setState(next);
      setFailed(false);
      return next;
    } catch {
      setFailed(true);
      return null;
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // While the trader is in Telegram: ask every few seconds until the chat is connected or the link has run out.
  useEffect(() => {
    if (!link) return;
    const timer = window.setInterval(() => {
      if (Date.now() > link.until) { setLink(null); return; }
      void load().then((next) => {
        if (next?.connected) {
          setLink(null);
          setNote({ tone: "ok", text: "Telegram is connected. A welcome message is in your chat." });
          changed.current?.();
        }
      });
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [link, load]);

  async function connect() {
    setBusy("connect"); setNote(null);
    // Open the tab inside the tap itself (a browser blocks a window opened later), then point it at the link.
    const tab = typeof window.open === "function" ? window.open("", "_blank") : null;
    try {
      const made = await api.telegramConnectStart();
      if (tab) { tab.opener = null; tab.location.href = made.url; }
      setLink({ url: made.url, until: Math.min(Date.now() + WAIT_MAX_MS, new Date(made.expires_at).getTime() || Date.now() + WAIT_MAX_MS) });
    } catch (e) {
      tab?.close();
      setNote({ tone: "problem", text: problem(e, "Telegram could not be opened just now. Try again in a moment.") });
    } finally {
      setBusy(null);
    }
  }

  async function sendTest() {
    setBusy("test"); setNote(null);
    try {
      const r = await api.testAlertChannel("telegram");
      setNote(r.ok ? { tone: "ok", text: "Test sent. Check your Telegram chat." }
        : { tone: "problem", text: "The test did not reach Telegram. If you blocked the bot, open it again and press Start." });
      await load();
      changed.current?.();
    } catch (e) {
      setNote({ tone: "problem", text: problem(e, "The test could not be sent just now. Try again in a moment.") });
    } finally {
      setBusy(null);
    }
  }

  async function disconnect() {
    setBusy("disconnect"); setNote(null);
    try {
      await api.deleteAlertChannel("telegram");
      setConfirmOff(false);
      setNote({ tone: "ok", text: "Telegram is disconnected. No more alerts go there." });
      await load();
      changed.current?.();
    } catch (e) {
      setNote({ tone: "problem", text: problem(e, "Telegram could not be disconnected just now. Try again in a moment.") });
    } finally {
      setBusy(null);
    }
  }

  if (failed && !state) return <p className="text-t14 text-fg-muted">Telegram could not be loaded. Try again in a moment.</p>;
  if (!state) return <div aria-busy="true" className="h-20 animate-pulse rounded-control bg-surface-2"><span className="sr-only">Loading</span></div>;

  const view = telegramView(state, link !== null);
  return (
    <div className="space-y-space-2" data-telegram={view}>
      <div className="flex items-start gap-space-3">
        <Send size={18} aria-hidden className="mt-px shrink-0 text-fg-muted" />
        <div className="min-w-0 flex-1">
          {title && (
            <p className="flex flex-wrap items-center gap-space-2 text-t15 font-semibold text-fg">
              Telegram
              {view === "connected" && <ChipV2 kind="brand"><Check size={12} aria-hidden /> Connected</ChipV2>}
            </p>
          )}
          <p className="text-t14 text-fg-muted">{TELEGRAM_TEXT[view]}</p>
        </div>
      </div>
      <div className="flex flex-col gap-space-2 sm:flex-row sm:flex-wrap">
        {view === "connect" && (
          <ActionV2 primary busy={busy === "connect"} disabled={busy !== null} onClick={() => void connect()}>
            <Send size={16} aria-hidden /> Connect Telegram
          </ActionV2>
        )}
        {view === "waiting" && link && (
          <>
            <a href={link.url} target="_blank" rel="noopener noreferrer" className={LINK_PRIMARY}>Open Telegram</a>
            <ActionV2 onClick={() => setLink(null)}>Cancel</ActionV2>
          </>
        )}
        {(view === "connected" || view === "own_bot") && (
          <ActionV2 busy={busy === "test"} disabled={busy !== null} onClick={() => void sendTest()}>Send a test</ActionV2>
        )}
        {view === "connected" && !confirmOff && (
          <ActionV2 disabled={busy !== null} onClick={() => setConfirmOff(true)}>Disconnect</ActionV2>
        )}
      </div>
      {confirmOff && (
        <div className="space-y-space-2 rounded-control border border-subtle p-space-3" role="group" aria-label="Disconnect Telegram">
          <p className="text-t14 text-fg">Stop sending alerts to Telegram? You can connect again any time.</p>
          <div className="flex flex-col gap-space-2 sm:flex-row">
            <ActionV2 busy={busy === "disconnect"} disabled={busy !== null} onClick={() => void disconnect()}>Yes, disconnect</ActionV2>
            <button type="button" className={LINK_SECONDARY} onClick={() => setConfirmOff(false)}>Keep it</button>
          </div>
        </div>
      )}
      {note && <Note tone={note.tone}>{note.text}</Note>}
    </div>
  );
}

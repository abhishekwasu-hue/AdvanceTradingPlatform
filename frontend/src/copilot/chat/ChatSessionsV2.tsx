import { Check, ChevronDown, History, Pencil, Plus, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { formatDateTimeIST } from "../../ui/format";
import { useCopilotT } from "../i18n";
import { chatApi, type Conversation, type ConversationItem } from "./model";

/**
 * CH-2 (owner 5 Nov, "Ho finish all job"): Claude-like chat sessions - "New chat" and the list of his earlier
 * conversations (titled by their first question, most recently used first): open one, rename it, delete it. Only his
 * own (the server answers 404 for any other). The same bar on Copilot > Ask and in the chat bubble.
 */
const ICON_BTN = "inline-flex min-h-11 min-w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg md:min-h-8 md:min-w-8";
const BAR_BTN = "inline-flex min-h-11 items-center gap-space-1 rounded-pill border border-border bg-surface-1 px-space-3 text-t13 text-fg hover:bg-surface-2 disabled:opacity-50 md:min-h-9";

export function ChatSessionsV2({ current, busy, onNew, onOpen, onRenamed, onDeleted }: {
  current: Conversation | null; busy: boolean;
  onNew: () => void; onOpen: (id: number) => void; onRenamed: (c: Conversation) => void; onDeleted: (id: number) => void;
}) {
  const t = useCopilotT();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ConversationItem[] | null>(null);
  const [error, setError] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [asking, setAsking] = useState<number | null>(null);

  const load = useCallback(() => {
    setError(false);
    chatApi.sessions().then((r) => setItems(r.sessions), () => setError(true));
  }, []);
  useEffect(() => { if (open) load(); }, [open, load]);

  async function rename(e: FormEvent, id: number) {
    e.preventDefault();
    const name = title.trim();
    if (!name) return;
    try {
      const done = await chatApi.renameSession(id, name);
      setItems((all) => all?.map((s) => (s.id === id ? { ...s, title: done.title } : s)) ?? all);
      onRenamed(done);
      setEditing(null);
    } catch { setError(true); }
  }

  async function remove(id: number) {
    try {
      await chatApi.deleteSession(id);
      setItems((all) => all?.filter((s) => s.id !== id) ?? all);
      setAsking(null);
      onDeleted(id);
    } catch { setError(true); }
  }

  return (
    <div className="flex flex-col gap-space-2" data-chat-sessions>
      <div className="flex flex-wrap items-center gap-space-2">
        <button type="button" className={BAR_BTN} disabled={busy} onClick={() => { setOpen(false); onNew(); }} data-new-chat>
          <Plus size={16} aria-hidden /> {t("chat.newChat")}
        </button>
        <button type="button" className={`${BAR_BTN} min-w-0 max-w-full`} aria-expanded={open} onClick={() => setOpen((v) => !v)} data-chat-sessions-toggle>
          <History size={16} aria-hidden className="shrink-0" />
          <span className="truncate">{current ? current.title : t("chat.conversations")}</span>
          <ChevronDown size={16} aria-hidden className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
      </div>
      {open && (
        <div className="rounded-card border border-border bg-surface-1 p-space-2" data-chat-sessions-list>
          <p className="px-space-2 pb-space-1 text-t12 font-semibold uppercase tracking-wide text-fg-muted">{t("chat.conversations")}</p>
          {error && <p className="px-space-2 py-space-1 text-t13 text-danger" role="alert">{t("chat.conversationsFailed")}</p>}
          {items === null && !error && <p className="px-space-2 py-space-1 text-t13 text-fg-muted">…</p>}
          {items !== null && items.length === 0 && <p className="px-space-2 py-space-1 text-t13 text-fg-muted">{t("chat.conversationsEmpty")}</p>}
          <ul className="flex max-h-72 flex-col overflow-y-auto">
            {(items ?? []).map((s) => {
              const here = current?.id === s.id;
              return (
                <li key={s.id} data-chat-session={s.id} className={`flex items-center gap-space-1 rounded-control ${here ? "bg-surface-2" : ""}`}>
                  {editing === s.id ? (
                    <form className="flex min-w-0 flex-1 items-center gap-space-1 p-space-1" onSubmit={(e) => void rename(e, s.id)}>
                      <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} autoFocus aria-label={t("chat.renameLabel")}
                        className="min-h-11 min-w-0 flex-1 rounded-control border border-border bg-surface-0 px-space-2 text-t14 text-fg md:min-h-8" />
                      <button type="submit" className={ICON_BTN} aria-label={t("chat.save")}><Check size={16} aria-hidden /></button>
                      <button type="button" className={ICON_BTN} aria-label={t("chat.cancel")} onClick={() => setEditing(null)}><X size={16} aria-hidden /></button>
                    </form>
                  ) : asking === s.id ? (
                    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-space-2 p-space-2">
                      <span className="text-t13 text-fg">{t("chat.deleteConfirm")}</span>
                      <button type="button" className={`${BAR_BTN} text-danger`} onClick={() => void remove(s.id)} data-confirm-delete>{t("chat.deleteConversation")}</button>
                      <button type="button" className={BAR_BTN} onClick={() => setAsking(null)}>{t("chat.cancel")}</button>
                    </div>
                  ) : (
                    <>
                      <button type="button" disabled={busy} onClick={() => { setOpen(false); onOpen(s.id); }}
                        className="flex min-h-11 min-w-0 flex-1 flex-col items-start px-space-2 py-space-1 text-left hover:bg-surface-2 disabled:opacity-50 md:min-h-9">
                        <span className="w-full truncate text-t14 text-fg">{s.title}</span>
                        <span className="text-t12 text-fg-muted">
                          {here ? `${t("chat.current")} · ` : ""}{t("chat.conversationTurns", { count: s.turns })}{s.updated_at ? ` · ${formatDateTimeIST(s.updated_at)}` : ""}
                        </span>
                      </button>
                      <button type="button" className={ICON_BTN} aria-label={`${t("chat.rename")}: ${s.title}`} onClick={() => { setEditing(s.id); setTitle(s.title); }}>
                        <Pencil size={15} aria-hidden />
                      </button>
                      <button type="button" className={ICON_BTN} aria-label={`${t("chat.deleteConversation")}: ${s.title}`} onClick={() => setAsking(s.id)}>
                        <Trash2 size={15} aria-hidden />
                      </button>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

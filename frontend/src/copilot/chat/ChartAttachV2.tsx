import { ImagePlus, Link2, Paperclip } from "lucide-react";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useCopilotT } from "../i18n";
import type { ChartSource } from "./model";

/**
 * M59 CR-2 (owner order ATP_ORDER_M59): a chart in the Copilot's Ask box. The attach button opens two choices - "Upload
 * a chart" (PNG / JPEG; a screenshot pasted in the box works too) and "Paste a TradingView snapshot link" - and hands
 * the chart to the SAME chart_analyze pipeline as the Analyze a chart page (POST /api/chart-analyze/items, then
 * /analyze; Image only). The result opens as a thread turn: the annotated chart and the reading (ResultViewV2), then the
 * Copilot's own structure card for the symbol (CR-1) - ./ChartTurnV2.tsx, its own chunk. No live-chart scraping;
 * nothing here places an order (ADR-0006).
 */
export const ACCEPT = "image/png,image/jpeg";

const ITEM = "flex min-h-11 w-full items-center gap-space-2 rounded-control px-space-3 text-left text-t14 text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";

/** A chart image from a paste / drop / file list, or null (PNG and JPEG only, as the server accepts). */
export function imageFrom(files: FileList | File[] | null | undefined): File | null {
  for (const f of Array.from(files ?? [])) if (f.type === "image/png" || f.type === "image/jpeg") return f;
  return null;
}

export function AttachMenu({ onAttach, disabled = false, startOpen = false }: { onAttach: (source: ChartSource) => void; disabled?: boolean; startOpen?: boolean }) {
  const t = useCopilotT();
  const [open, setOpen] = useState(startOpen);
  const [linkOpen, setLinkOpen] = useState(false);
  const [link, setLink] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const linkId = useId();
  const menuId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); setLinkOpen(false); } };
    const onDown = (e: PointerEvent) => { if (box.current && !box.current.contains(e.target as Node)) { setOpen(false); setLinkOpen(false); } };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open]);

  const close = () => { setOpen(false); setLinkOpen(false); setLink(""); };
  const submitLink = (e: FormEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const value = link.trim();
    if (!value) return;
    close();
    onAttach({ kind: "link", link: value });
  };

  return (
    <div ref={box} className="relative shrink-0" data-chart-attach>
      <button type="button" aria-label={t("chat.attach")} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined}
        disabled={disabled} onClick={() => setOpen((o) => !o)} data-attach-button
        className="inline-flex h-11 w-11 items-center justify-center rounded-control border border-border bg-surface-2 text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50">
        <Paperclip size={18} aria-hidden />
      </button>
      <input ref={fileInput} type="file" accept={ACCEPT} className="sr-only" tabIndex={-1} aria-hidden data-attach-file
        onChange={(e) => { const f = imageFrom(e.target.files); e.target.value = ""; if (f) { close(); onAttach({ kind: "file", file: f, name: f.name }); } }} />
      {open && (
        <div id={menuId} role="menu" aria-label={t("chat.attach")} data-attach-menu
          className="absolute bottom-full left-0 z-20 mb-space-2 w-72 max-w-[calc(100vw-2rem)] space-y-space-1 rounded-panel border border-border bg-surface-1 p-space-2 shadow-raised">
          <button type="button" role="menuitem" className={ITEM} onClick={() => fileInput.current?.click()} data-attach="upload">
            <ImagePlus size={16} aria-hidden className="shrink-0 text-fg-muted" />{t("chat.attachUpload")}
          </button>
          <button type="button" role="menuitem" className={ITEM} aria-expanded={linkOpen} onClick={() => setLinkOpen((v) => !v)} data-attach="link">
            <Link2 size={16} aria-hidden className="shrink-0 text-fg-muted" />{t("chat.attachLink")}
          </button>
          {linkOpen && (
            <div className="space-y-space-1 px-space-1 pb-space-1" data-attach-link-form>
              <label htmlFor={linkId} className="sr-only">{t("chat.attachLinkLabel")}</label>
              <div className="flex items-center gap-space-2">
                <input id={linkId} type="url" inputMode="url" value={link} autoComplete="off" spellCheck={false} placeholder="https://www.tradingview.com/x/..."
                  onChange={(e) => setLink(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") submitLink(e); }}
                  className="h-11 min-w-0 flex-1 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9" />
                <button type="button" onClick={submitLink} disabled={!link.trim()} data-attach-go
                  className="inline-flex h-11 shrink-0 items-center rounded-control bg-brand px-space-3 text-t14 font-semibold text-on-brand disabled:opacity-50 md:h-9">
                  {t("chat.attachGo")}
                </button>
              </div>
            </div>
          )}
          <p className="px-space-3 pb-space-1 text-t12 text-fg-muted" data-attach-help>{t("chat.attachHelp")}</p>
        </div>
      )}
    </div>
  );
}

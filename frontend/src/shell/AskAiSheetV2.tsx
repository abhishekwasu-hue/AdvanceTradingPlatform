import { ArrowLeft, ChevronRight, MessagesSquare, ScanSearch, ScanText, Table2 } from "lucide-react";
import { Suspense, lazy, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { Sheet } from "../components/primitives/Dialog";
import { expiryLabel } from "../trading/model";
import { Skeleton } from "../components/primitives";
import type { AskAiOption, AskAiOptionId, AskAiPageContext } from "./askAiEntry";

/**
 * CT-1 C10: the one Ask AI sheet (phone: from the bottom; desktop: a side panel). On top, what the page on screen is
 * about (symbol, timeframe or expiry); then one row per option (src/shell/askAiEntry.tsx). "Ask about this chain"
 * opens the chain's existing Ask AI inside the sheet, with a way back to the list.
 */
const AskAi = lazy(() => import("../optionChain/AskAiV2").then((m) => ({ default: m.AskAi })));

const ICONS: Record<AskAiOptionId, ReactNode> = {
  read: <ScanText size={18} aria-hidden />,
  chain: <Table2 size={18} aria-hidden />,
  analyze: <ScanSearch size={18} aria-hidden />,
  copilot: <MessagesSquare size={18} aria-hidden />,
};

const ROW = "flex min-h-11 w-full items-center gap-space-3 rounded-control border border-border bg-surface-1 px-space-3 py-space-2 text-left hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

/** The page's context as chips: "NIFTY", "15 min" or "Expiry 3 Nov". */
export function contextChips(ctx: AskAiPageContext | null): string[] {
  if (!ctx) return [];
  const out: string[] = [];
  if (ctx.symbol) out.push(ctx.symbol);
  if (ctx.timeframe) out.push(ctx.timeframe);
  const exp = expiryLabel(ctx.expiry ?? ctx.chain?.expiry ?? null);
  if (exp) out.push(`Expiry ${exp}`);
  return out;
}

export interface AskAiSheetV2Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  ctx: AskAiPageContext | null;
  options: AskAiOption[];
  phone: boolean;
}

/** The sheet's content (exported for tests): the context chips, then the list or the chain's Ask AI. */
export function AskAiSheetBody({ ctx, options, view, onView, onClose }: {
  ctx: AskAiPageContext | null;
  options: AskAiOption[];
  view: "list" | "chain";
  onView: (view: "list" | "chain") => void;
  onClose: () => void;
}) {
  const chain = ctx?.chain ?? null;
  const shown = view === "chain" && chain ? "chain" : "list";
  const chips = contextChips(ctx);

  const row = (o: AskAiOption) => {
    const body = (
      <>
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-control bg-brand/10 text-brand">{ICONS[o.id]}</span>
        <span className="min-w-0 flex-1">
          <span className="block text-t14 font-semibold text-fg">{o.label}</span>
          <span className="block text-t12 text-fg-muted">{o.hint}</span>
        </span>
        <ChevronRight size={16} aria-hidden className="shrink-0 text-fg-muted" />
      </>
    );
    if (o.to) return <Link key={o.id} to={o.to} onClick={onClose} className={ROW} data-ask-option={o.id}>{body}</Link>;
    const act = () => {
      if (o.id === "chain") { onView("chain"); return; }
      if (o.id === "read") { ctx?.read?.toggle(); onClose(); }
    };
    return <button key={o.id} type="button" onClick={act} className={ROW} data-ask-option={o.id} aria-pressed={o.id === "read" ? !!ctx?.read?.on : undefined}>{body}</button>;
  };

  return (
    <div data-ask-ai-sheet={shown} className="space-y-space-3">
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-space-2" data-ask-context>
          {chips.map((c) => (
            <span key={c} className="rounded-pill border border-subtle bg-surface-2 px-space-2 py-space-1 text-t12 font-semibold text-fg">{c}</span>
          ))}
        </div>
      )}
      {shown === "list" ? (
        <div className="space-y-space-2">{options.map(row)}</div>
      ) : chain && (
        <>
          <button type="button" onClick={() => onView("list")}
            className="inline-flex min-h-11 items-center gap-space-1 rounded-control text-t14 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
            <ArrowLeft size={16} aria-hidden /> All AI tools
          </button>
          <Suspense fallback={<Skeleton className="h-40" panel label="Loading" />}>
            <AskAi symbol={chain.symbol} expiry={chain.expiry} refreshKey={chain.refreshKey} />
          </Suspense>
        </>
      )}
    </div>
  );
}

export default function AskAiSheetV2({ open, onOpenChange, ctx, options, phone }: AskAiSheetV2Props) {
  const [view, setView] = useState<"list" | "chain">("list");
  // Each opening starts at the list.
  useEffect(() => { if (!open) setView("list"); }, [open]);
  const shown = view === "chain" && ctx?.chain ? "chain" : "list";
  return (
    <Sheet side={phone ? "bottom" : "right"} open={open} onOpenChange={onOpenChange}
      title={shown === "chain" ? "Ask about this chain" : "Ask AI"}>
      <AskAiSheetBody ctx={ctx} options={options} view={view} onView={setView} onClose={() => onOpenChange(false)} />
    </Sheet>
  );
}

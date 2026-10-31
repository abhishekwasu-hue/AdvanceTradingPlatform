import { Copy, Download, Share2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { CARD, CardV2, LINK_SECONDARY, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { growthApi } from "./api";
import { CARD_H, CARD_W, SHARE_FILE, cardSvg, cleanCard, resolveTokens, shareText } from "./model";
import type { DisciplineCard } from "./types";
import { Details } from "../ui/Details";

/**
 * MO4 slice 3 (`ui_v2` + `growth_v2`): "My discipline this week" - a card he can save as an image or share, built from
 * his own PAPER record with discipline facts only (days inside his own limits, his journal streak, the discipline score
 * when the journal is on, the days he traded said neutrally). Never a stock name, a price, money or a call; the card
 * never praises trading more. Nothing leaves the device unless he taps Share or Download himself; no link to his data
 * is created. The parent mounts this only while both flags are on.
 */

type State = { status: "loading" } | { status: "error" } | { status: "ok"; data: DisciplineCard };

const BUTTON = LINK_SECONDARY;

function readToken(token: string): string {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue(`--${token}`);
  } catch {
    return "";
  }
}

/** The card drawn to a PNG with the current theme's colours. */
async function cardPng(card: DisciplineCard): Promise<Blob> {
  return svgPng(cardSvg(card));
}

/** Card markup (CARD_W x CARD_H) drawn to a PNG on this device with the current theme's colours; nothing is uploaded.
 * AUD-T4: shared with the paper report card. */
export async function svgPng(markup: string): Promise<Blob> {
  const svg = resolveTokens(markup, readToken);
  const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
  try {
    const img = new Image();
    await new Promise<void>((ok, fail) => { img.onload = () => ok(); img.onerror = () => fail(new Error("image")); img.src = url; });
    const canvas = document.createElement("canvas");
    canvas.width = CARD_W;
    canvas.height = CARD_H;
    canvas.getContext("2d")?.drawImage(img, 0, 0, CARD_W, CARD_H);
    return await new Promise<Blob>((ok, fail) => canvas.toBlob((b) => (b ? ok(b) : fail(new Error("png"))), "image/png"));
  } finally {
    URL.revokeObjectURL(url);
  }
}

function save(blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = SHARE_FILE;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function DisciplineCardBodyV2({ card }: { card: DisciplineCard }) {
  const shown = useMemo(() => cleanCard(card), [card]);
  const svg = useMemo(() => cardSvg(shown), [shown]);
  const [note, setNote] = useState<string | null>(null);
  const canShare = typeof navigator !== "undefined" && typeof navigator.share === "function";

  async function onDownload() {
    setNote(null);
    try { save(await cardPng(shown)); setNote("Saved as an image."); } catch { setNote("Could not make the image on this device."); }
  }
  async function onShare() {
    setNote(null);
    try {
      const file = new File([await cardPng(shown)], SHARE_FILE, { type: "image/png" });
      const data: ShareData = { title: shown.title, text: shareText(shown) };
      if (navigator.canShare?.({ files: [file] })) data.files = [file];
      await navigator.share(data);
    } catch { /* he closed the share sheet */ }
  }
  async function onCopy() {
    try { await navigator.clipboard.writeText(shareText(shown)); setNote("Copied."); } catch { setNote("Could not copy on this device."); }
  }

  return (
    <div>
      <Details id="growth.discipline" className="mt-0"><p className="text-t14 text-fg-muted">Discipline facts from your own paper trading. No stock names, no prices, no advice.</p></Details>
      <div className={`${CARD} mt-space-3 overflow-hidden`} data-share-card>
        {/* The same markup the image is made from; its colours follow the theme. */}
        <div className="w-full [&>svg]:h-auto [&>svg]:w-full" dangerouslySetInnerHTML={{ __html: svg }} />
      </div>
      {card.hints.length > 0 && (
        <ul className="mt-space-3 space-y-space-1">
          {card.hints.map((h) => <li key={h} className="text-t13 text-fg-muted">{h}</li>)}
        </ul>
      )}
      <div className="mt-space-3 flex flex-wrap gap-space-2">
        {canShare && <button type="button" className={BUTTON} onClick={onShare} data-share><Share2 size={16} aria-hidden />Share</button>}
        <button type="button" className={BUTTON} onClick={onDownload} data-download><Download size={16} aria-hidden />Save image</button>
        <button type="button" className={BUTTON} onClick={onCopy} data-copy><Copy size={16} aria-hidden />Copy text</button>
        <Link to="/risk-management" className="inline-flex h-11 items-center text-t14 font-medium text-brand hover:underline md:h-9">Your own limits</Link>
      </div>
      {note && <p className="mt-space-2 text-t12 text-fg-muted" role="status">{note}</p>}
    </div>
  );
}

export default function DisciplineCardV2() {
  const [state, setState] = useState<State>({ status: "loading" });
  useEffect(() => {
    let live = true;
    growthApi.disciplineWeek().then((data) => { if (live) setState({ status: "ok", data }); }, () => { if (live) setState({ status: "error" }); });
    return () => { live = false; };
  }, []);
  return (
    <CardV2 testId="discipline-card" title="Share my week" className="mb-space-4">
      {state.status === "loading" ? <LoadingRowsV2 rows={3} /> : state.status === "error" ? <LoadFailedV2 what="your week" />
        : <DisciplineCardBodyV2 card={state.data} />}
    </CardV2>
  );
}

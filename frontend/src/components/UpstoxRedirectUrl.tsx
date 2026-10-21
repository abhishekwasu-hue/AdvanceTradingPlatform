import { TriangleAlert } from "lucide-react";
import { useEffect, useId, useRef, useState, type Ref } from "react";
import { api } from "../api/client";
import type { UpstoxRedirectInfo } from "../types";

/**
 * D2: the Upstox Redirect URL on the Connect broker screen (Settings, in both shells). The address comes from the
 * server (GET /broker/upstox/redirect-url: the deployment's public address plus the callback path), never from the
 * browser's address bar, so it is the same however the page was opened. A stored Redirect URI that differs is
 * reported, not changed: the trader updates it in the Upstox developer app and stores the credentials here again.
 */

export type CopyState = "idle" | "copied" | "selected";

/** Copies `text` with the Clipboard API; when that is missing or refused, `selectText` selects the address so it can be
 * copied by hand. Never throws. */
export async function copyRedirectUrl(
  text: string, clipboard: Pick<Clipboard, "writeText"> | undefined, selectText: () => void,
): Promise<Exclude<CopyState, "idle">> {
  try {
    if (!clipboard) throw new Error("Clipboard unavailable");
    await clipboard.writeText(text);
    return "copied";
  } catch {
    try { selectText(); } catch { /* nothing more to do: the address is still shown */ }
    return "selected";
  }
}

/** The labels of the stored Upstox accounts whose Redirect URI differs from the public one. */
export function mismatchedAccounts(info: UpstoxRedirectInfo): string[] {
  return info.accounts.filter((a) => a.redirect_uri_mismatch).map((a) => a.account_label);
}

/** Shown while Upstox is the selected broker, and whenever a stored Upstox Redirect URI differs (so the note is not
 * hidden behind another broker). Nothing until the address is known. */
export function showRedirectBlock(info: UpstoxRedirectInfo | null, selected: boolean): info is UpstoxRedirectInfo {
  return info !== null && (selected || info.redirect_uri_mismatch);
}

export function UpstoxRedirectUrlView({ info, copyState = "idle", onCopy, inputRef }: {
  info: UpstoxRedirectInfo;
  copyState?: CopyState;
  onCopy?: () => void;
  inputRef?: Ref<HTMLInputElement>;
}) {
  const fieldId = useId();
  const mismatched = mismatchedAccounts(info);
  const accounts = mismatched.length
    ? ` (${mismatched.length === 1 ? "account" : "accounts"} ${mismatched.map((l) => `"${l}"`).join(", ")})` : "";
  return (
    <div data-upstox-redirect className="rounded-lg border border-border bg-surface-2/40 p-3 space-y-2">
      <label htmlFor={fieldId} className="block text-xs font-medium text-fg">Redirect URL</label>
      <div className="flex gap-2">
        <input
          id={fieldId}
          ref={inputRef}
          readOnly
          className="flex-1 min-w-0 rounded bg-surface-2 border border-border px-2 py-1.5 text-xs font-mono text-fg"
          value={info.redirect_url}
          onFocus={(e) => e.target.select()}
        />
        <button
          type="button"
          onClick={onCopy}
          className="rounded border border-border hover:bg-surface-2 text-fg px-3 py-1.5 text-xs shrink-0"
        >
          {copyState === "copied" ? "Copied" : "Copy"}
        </button>
      </div>
      <p aria-live="polite" className="text-xs text-fg-muted">
        {copyState === "copied" && "Copied to the clipboard. "}
        {copyState === "selected" && "The address is selected: copy it with your keyboard or the browser menu. "}
        Paste this address into the Redirect URL field of your app in the Upstox developer portal.
      </p>
      {info.redirect_uri_mismatch && (
        <p role="status" data-redirect-mismatch className="flex items-start gap-2 rounded bg-warn-tint px-3 py-2 text-xs text-warn">
          <TriangleAlert size={14} aria-hidden className="mt-0.5 shrink-0" />
          <span>
            The Redirect URI saved for Upstox{accounts} differs from this address. Update the Redirect URL in your
            Upstox app and store the credentials here again with this address in the Redirect URI field.
          </span>
        </p>
      )}
    </div>
  );
}

/** The block on Settings (see showRedirectBlock). `refreshKey` changes after credentials are stored or removed. */
export default function UpstoxRedirectUrl({ selected, refreshKey = 0 }: { selected: boolean; refreshKey?: number }) {
  const [info, setInfo] = useState<UpstoxRedirectInfo | null>(null);
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const inputRef = useRef<HTMLInputElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let live = true;
    api.upstoxRedirectUrl().then((r) => { if (live) setInfo(r); }).catch(() => { if (live) setInfo(null); });
    return () => { live = false; };
  }, [refreshKey]);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  if (!showRedirectBlock(info, selected)) return null;

  async function handleCopy() {
    if (!info) return;
    const clipboard = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
    const outcome = await copyRedirectUrl(info.redirect_url, clipboard, () => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
    setCopyState(outcome);
    if (timer.current) clearTimeout(timer.current);
    if (outcome === "copied") timer.current = setTimeout(() => setCopyState("idle"), 2000);
  }

  return <UpstoxRedirectUrlView info={info} copyState={copyState} onCopy={handleCopy} inputRef={inputRef} />;
}

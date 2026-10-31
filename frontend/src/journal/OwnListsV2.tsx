import { useEffect, useState } from "react";
import { errorSentence } from "../api/errors";
import { useToast } from "../components/Toast";
import { CardV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_PRIMARY, FIELD, LABEL } from "../trading/PartsV2";
import { ErrorState } from "../ui/ErrorState";
import type { JournalApi } from "./api";
import { TEXTAREA } from "./JournalPartsV2";
import { lines } from "./journalModel";
import type { JournalSettingsResponse } from "./types";

/**
 * M62 JN-3: his own lists for the journal - the mistake / emotion tags he picks from on an entry, the rules of his
 * checklist (yes / no on each entry) and the largest image he may attach. One item per line; saved with the journal
 * settings (PUT /journal/settings sends only these keys, the rest keep their value).
 */
export function OwnListsV2({ api, onSaved }: { api: Pick<JournalApi, "settings" | "saveSettings">; onSaved?: (s: JournalSettingsResponse) => void }) {
  const toast = useToast();
  const [data, setData] = useState<JournalSettingsResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tags, setTags] = useState("");
  const [rules, setRules] = useState("");
  const [mb, setMb] = useState("");
  const [busy, setBusy] = useState(false);
  const [round, setRound] = useState(0);
  useEffect(() => {
    let alive = true;
    api.settings().then((d) => {
      if (!alive) return;
      setData(d); setError(null);
      setTags((d.settings.custom_tags ?? []).join("\n"));
      setRules((d.settings.checklist_rules ?? []).join("\n"));
      setMb(String(d.settings.attachment_max_mb ?? ""));
    }).catch((e) => { if (alive) setError(e); });
    return () => { alive = false; };
  }, [api, round]);
  if (error) return <CardV2 title="Your tags and rules" testId="own-lists"><ErrorState message={error} onRetry={() => setRound((r) => r + 1)} variant="inline" /></CardV2>;
  if (!data) return <CardV2 title="Your tags and rules" testId="own-lists"><LoadingRowsV2 rows={3} /></CardV2>;
  const range = data.ranges.attachment_max_mb;
  async function save() {
    const size = Number(mb);
    if (!Number.isFinite(size) || (range && (size < range[0] || size > range[1]))) {
      toast.error(`Largest image: enter a number${range ? ` from ${range[0]} to ${range[1]}` : ""} (MB).`);
      return;
    }
    setBusy(true);
    try {
      const d = await api.saveSettings({ custom_tags: lines(tags), checklist_rules: lines(rules), attachment_max_mb: size });
      setData(d);
      setTags((d.settings.custom_tags ?? []).join("\n"));
      setRules((d.settings.checklist_rules ?? []).join("\n"));
      toast.success("Your tags and rules are saved.");
      onSaved?.(d);
    } catch (e) { toast.error(errorSentence(e)); } finally { setBusy(false); }
  }
  return (
    <CardV2 title="Your tags and rules" testId="own-lists">
      <div className="grid gap-space-3 md:grid-cols-2">
        <label className="min-w-0">
          <span className={LABEL}>Your tags (one per line)</span>
          <textarea className={`${TEXTAREA} h-32`} value={tags} onChange={(e) => setTags(e.target.value)} data-own-tags-input />
        </label>
        <label className="min-w-0">
          <span className={LABEL}>Your rules (one per line)</span>
          <textarea className={`${TEXTAREA} h-32`} value={rules} onChange={(e) => setRules(e.target.value)} data-own-rules-input />
        </label>
        <label className="min-w-0">
          <span className={LABEL}>Largest image (MB)</span>
          <input className={FIELD} type="number" step="any" inputMode="decimal" value={mb} onChange={(e) => setMb(e.target.value)} data-own-mb />
        </label>
      </div>
      <div className="mt-space-3 flex justify-end">
        <button type="button" className={BUTTON_PRIMARY} disabled={busy} onClick={save} data-own-save>{busy ? "Saving…" : "Save"}</button>
      </div>
    </CardV2>
  );
}

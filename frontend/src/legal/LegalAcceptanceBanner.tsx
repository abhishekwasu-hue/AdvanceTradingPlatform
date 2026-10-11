import { FileText } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Button } from "../components/primitives";
import { clearLater, deferLater, deferredFor, laterKey, legalPath, listTitles, versionsFor, type LegalAcceptanceStatus } from "./legal";

/** Z10-f: asks a signed-in user to accept the current Terms of use, Privacy notice and Risk disclosure when they have
 * not yet (accounts from before the pages existed, or after a new version). A notice above the page - never a modal:
 * positions, exits and every other screen keep working while it is open. "Later" hides it until the next sign-in. */
export default function LegalAcceptanceBanner() {
  const { user, loading } = useAuth();
  const [status, setStatus] = useState<LegalAcceptanceStatus | null>(null);
  const [hidden, setHidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStatus(null);
    setHidden(false);
    if (!user) { if (!loading) clearLater(); return; }       // signed out (not just still loading)
    let live = true;
    api.legalAcceptance().then((s) => { if (live) setStatus(s); }).catch(() => { /* best effort: never in the way */ });
    return () => { live = false; };
  }, [user, loading]);

  if (!user || !status || status.all_accepted || hidden) return null;
  const versions = versionsFor(status.documents, status.pending);
  const key = laterKey(user.id, status.pending, versions);
  if (deferredFor(key)) return null;

  async function accept() {
    setBusy(true);
    setError(null);
    try {
      setStatus(await api.acceptLegal(versions));
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ""));
      api.legalAcceptance().then(setStatus).catch(() => undefined);     // a new version appeared: show it
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="px-3 pt-3 md:px-6">
      <section aria-label="Updated legal documents"
               className="flex flex-wrap items-center gap-3 rounded-lg border border-border border-l-4 border-l-brand bg-surface-1 px-3 py-2 text-sm text-fg">
        <FileText size={16} className="shrink-0 text-brand" aria-hidden />
        <p className="min-w-0 flex-1">
          Please read and accept the {listTitles(status.pending)}:{" "}
          {status.pending.map((slug, i) => (
            <span key={slug}>
              {i > 0 && ", "}
              <Link to={legalPath(slug)} target="_blank" rel="noopener" className="text-brand underline-offset-2 hover:underline">
                {status.documents.find((d) => d.slug === slug)?.title ?? slug}
              </Link>
            </span>
          ))}
          . Nothing is blocked meanwhile.
        </p>
        <Button variant="primary" className="h-11 md:h-9" loading={busy} onClick={() => void accept()}>I have read and accept</Button>
        <Button variant="ghost" className="h-11 md:h-9" onClick={() => { deferLater(key); setHidden(true); }}>Later</Button>
        {error && <p role="alert" className="w-full text-xs text-down">{error}</p>}
      </section>
    </div>
  );
}

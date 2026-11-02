import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { ApiError } from "../../api/errors";
import { SkeletonRows } from "../../components/primitives/Feedback";
import { LegalView } from "../../legal/LegalPage";
import { isLegalSlug, type LegalDocument } from "../../legal/legal";
import type { PanelProps } from "../registry";

/** NAV-6b: a legal document in place (a modal over the tab): the same text and view as `/legal/<slug>`, read the same
 * way; an unknown or unpublished document says so in the same words. */
export default function LegalPanel({ params }: PanelProps) {
  const slug = params.slug;
  const [doc, setDoc] = useState<LegalDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setDoc(null);
    setError(null);
    if (!isLegalSlug(slug)) { setError("This page does not exist."); return undefined; }
    let live = true;
    api.legalDocument(slug)
      .then((d) => { if (live) setDoc(d); })
      .catch((e) => { if (live) setError(e instanceof ApiError && e.status === 404 ? "This page does not exist." : String(e).replace(/^Error:\s*/, "")); });
    return () => { live = false; };
  }, [slug]);
  if (error) return <p className="text-t14 text-fg-muted" role="status">{error}</p>;
  if (!doc) return <SkeletonRows rows={4} label="Loading the document" />;
  return <LegalView doc={doc} />;
}

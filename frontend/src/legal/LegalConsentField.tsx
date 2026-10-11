import { LEGAL_SLUGS, LEGAL_TITLES, legalPath } from "./legal";

/** Z10-f: the sign-up (and team-join) form's acceptance box. Required: the form does not submit unticked. The links open
 * the public pages in a new tab so what was typed in the form stays. */
export default function LegalConsentField({ checked, onChange, id = "legal-consent" }: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  id?: string;
}) {
  return (
    <div className="flex items-start gap-2 text-xs text-fg-muted">
      <input id={id} type="checkbox" required checked={checked} onChange={(e) => onChange(e.target.checked)}
             className="mt-0.5 h-4 w-4 shrink-0 accent-[rgb(var(--brand))]" />
      <label htmlFor={id}>
        I have read and accept the{" "}
        {LEGAL_SLUGS.map((slug, i) => (
          <span key={slug}>
            {i > 0 && (i === LEGAL_SLUGS.length - 1 ? " and " : ", ")}
            <a href={legalPath(slug)} target="_blank" rel="noopener" className="text-brand underline-offset-2 hover:underline">{LEGAL_TITLES[slug]}</a>
          </span>
        ))}
        . I understand that the platform trades on paper only and gives no investment advice.
      </label>
    </div>
  );
}

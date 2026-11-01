import { KeyRound, Link2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { errorSentence } from "../api/errors";
import { FacebookMark, GoogleMark } from "../auth/SocialButtons";
import { takeSocialReturn } from "../auth/social";
import type { SignInIdentities } from "../auth/socialTypes";
import { Card } from "../components/ui";
import { formatDateIST } from "../ui/format";

/**
 * M73 SL-2 (behind `ui_v2`): Settings › Account "Ways to sign in" - the password and each linked provider, with Link /
 * Unlink. The last way to sign in cannot be unlinked (the server refuses it; the button is not offered either). Linking
 * goes through the provider and comes back here (?social=linked).
 */
export function canUnlink(state: SignInIdentities): boolean {
  return state.has_password || state.identities.length > 1;
}

function Mark({ id }: { id: string }) {
  if (id === "google") return <GoogleMark size={18} />;
  if (id === "facebook") return <FacebookOnSurface />;
  return <Link2 size={18} aria-hidden />;
}

/** The "f" sits on a Facebook-blue disc here (a light card would hide the white mark). */
function FacebookOnSurface() {
  return <span className="flex h-[18px] w-[18px] items-center justify-center rounded-full bg-social-facebook"><FacebookMark size={18} /></span>;
}

export default function SignInMethodsCardV2() {
  const [state, setState] = useState<SignInIdentities | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  function load() {
    api.signInIdentities().then(setState).catch((e) => setError(errorSentence(e)));
  }

  useEffect(() => {
    const back = takeSocialReturn(["linked", "error"]);
    if (back?.kind === "linked") setMessage("Linked. You can now sign in with it.");
    else if (back?.kind === "error") setError(back.message);
    load();
  }, []);

  async function link(id: string, slug: string) {
    setBusy(id);
    setError(null);
    try {
      const { url } = await api.socialLinkStart(slug);
      window.location.assign(url);
    } catch (e) {
      setError(errorSentence(e));
      setBusy(null);
    }
  }

  async function unlink(id: string) {
    setBusy(id);
    setError(null);
    setMessage(null);
    try {
      await api.socialUnlink(id);
      setMessage("Unlinked.");
      load();
    } catch (e) {
      setError(errorSentence(e));
    } finally {
      setBusy(null);
    }
  }

  if (!state) return error ? <Card title="Ways to sign in"><p role="alert" className="text-t12 text-down">{error}</p></Card> : null;
  const unlinkable = canUnlink(state);
  return (
    <Card title="Ways to sign in">
      <ul className="divide-y divide-border-subtle" data-sign-in-methods>
        <li className="flex min-h-12 items-center gap-space-3 py-space-2">
          <KeyRound size={18} aria-hidden className="text-fg-muted" />
          <span className="flex-1 text-t14 text-fg">Password</span>
          <span className={`text-t12 ${state.has_password ? "text-up" : "text-fg-muted"}`}>{state.has_password ? "Set" : "Not set"}</span>
        </li>
        {state.identities.map((i) => (
          <li key={i.provider} data-identity={i.provider} className="flex min-h-12 items-center gap-space-3 py-space-2">
            <Mark id={i.provider} />
            <span className="min-w-0 flex-1">
              <span className="block text-t14 text-fg">{i.label}</span>
              <span className="block truncate text-t12 text-fg-muted">{[i.email, i.linked_at && `since ${formatDateIST(i.linked_at)}`].filter(Boolean).join(" · ")}</span>
            </span>
            {unlinkable && (
              <button type="button" disabled={busy !== null} onClick={() => void unlink(i.provider)}
                className="h-11 rounded-control border border-border px-space-3 text-t14 text-fg hover:bg-surface-2 disabled:opacity-50">
                Unlink
              </button>
            )}
          </li>
        ))}
        {state.available.map((p) => (
          <li key={p.id} data-available={p.id} className="flex min-h-12 items-center gap-space-3 py-space-2">
            <Mark id={p.id} />
            <span className="flex-1 text-t14 text-fg">{p.label}</span>
            <button type="button" disabled={busy !== null} onClick={() => void link(p.id, p.slug)}
              className="h-11 rounded-control bg-brand px-space-3 text-t14 font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-50">
              Link
            </button>
          </li>
        ))}
      </ul>
      {!unlinkable && state.identities.length > 0 && (
        <p className="mt-space-2 text-t12 text-fg-muted">Your only way to sign in stays linked. Set a password with "Forgot password?" on the sign-in page to unlink it.</p>
      )}
      {message && <p role="status" className="mt-space-2 text-t12 text-up">{message}</p>}
      {error && <p role="alert" className="mt-space-2 text-t12 text-down">{error}</p>}
    </Card>
  );
}

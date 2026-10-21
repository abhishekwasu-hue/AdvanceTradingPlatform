import { ChevronRight, Plug } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { BROKER_PATH } from "../broker/paths";
import AlertChannelsCard from "../components/AlertChannelsCard";
import AiProviderCard from "../components/AiProviderCard";
import ApiKeysCard from "../components/ApiKeysCard";
import BillingCard from "../components/BillingCard";
import VisionByokCard from "../components/VisionByokCard";
import WebhookCard from "../components/WebhookCard";
import { Card } from "../components/ui";
import AppearanceCard from "../components/AppearanceCard";
import { PageHeader } from "../components/primitives";
import { SetupLinkV2 } from "../setup/SetupChipV2";
import { useFeatures } from "../ui/useFeatures";

/**
 * Settings. P2b: the broker moved to its own page (src/broker/BrokerPage.tsx) - Settings keeps only a link to it. What
 * the old broker area also held moved too: the TradingView webhook to Integrations here, the Vision key to AI here,
 * and the static IP, the Upstox redirect URL and the broker accounts to "Advanced" on the Broker page.
 */

export const BROKER_PAGE_PATH = BROKER_PATH;

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="space-y-4 scroll-mt-20">
      <h2 id={`${id}-title`} className="pt-2 text-sm font-semibold text-fg">{title}</h2>
      {children}
    </section>
  );
}

export function BrokerLinkCard() {
  return (
    <Card title="Broker">
      <Link to={BROKER_PAGE_PATH} data-broker-link
        className="-m-1 flex min-h-11 items-center gap-3 rounded-lg p-1 text-sm font-medium text-fg hover:bg-surface-2">
        <Plug size={18} aria-hidden className="shrink-0 text-fg-muted" />
        <span className="flex-1">Connect, renew or remove your broker on the Broker page</span>
        <ChevronRight size={16} aria-hidden className="shrink-0 text-fg-muted" />
      </Link>
    </Card>
  );
}

export default function SettingsPage() {
  const v2 = useFeatures().on("ui_v2");
  const { user, loading: authLoading } = useAuth();

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <PageHeader title="Settings" />
        <AppearanceCard />
        <Card>
          <p className="text-sm text-fg-muted">Log in from the Account tab to manage your settings.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Settings" />
      {/* UI2-7: Settings → Setup (the wizard and the go-live checklist), only while ui_v2 is on; off, nothing renders. */}
      {v2 && <SetupLinkV2 />}
      <AppearanceCard />
      <BrokerLinkCard />
      <BillingCard />
      <AlertChannelsCard />

      <Section id="integrations" title="Integrations">
        <WebhookCard />
        <ApiKeysCard />
      </Section>

      <Section id="ai" title="AI">
        <AiProviderCard />
        <VisionByokCard />
      </Section>
    </div>
  );
}

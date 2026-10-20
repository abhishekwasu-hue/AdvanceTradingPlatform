import type { Meta, StoryObj } from "@storybook/react";
import { BrokerGateView } from "../ui/BrokerGate";
import { tokenRow } from "../ui/brokerRows.fixture";
import { brokerGateState } from "../ui/brokerSession";

/** UI2-0: the broker gate in each state. Rendered with the view only (no API calls); the actions log nothing. */
const base = { authLoading: false, signedIn: true, failed: false };
const PAGE = (
  <div className="rounded-panel border border-subtle bg-surface-1 p-space-5 text-t14 text-fg">
    Connected: the page&apos;s own content renders here, unchanged.
  </div>
);

const meta: Meta<typeof BrokerGateView> = {
  title: "UI v2/Broker gate",
  component: BrokerGateView,
  args: { children: PAGE },
  parameters: { docs: { description: { component: "Never sample data in place of real data: a data page renders only when a broker session is usable." } } },
};
export default meta;
type Story = StoryObj<typeof BrokerGateView>;

export const NoBroker: Story = { name: "No broker: Connect your broker", args: { state: brokerGateState({ ...base, tokens: [] }) } };

export const LoginUpstox: Story = {
  name: "Login needed: Upstox (OAuth)",
  args: { state: brokerGateState({ ...base, tokens: [tokenRow("upstox")] }) },
};

export const LoginAngelOne: Story = {
  name: "Login needed: Angel One (TOTP)",
  args: { state: brokerGateState({ ...base, tokens: [tokenRow("angel_one", { token_status: "UNKNOWN" })] }) },
};

const zerodha = tokenRow("zerodha");
export const LoginZerodhaCode: Story = {
  name: "Login needed: Zerodha, paste the code",
  args: {
    state: brokerGateState({ ...base, tokens: [zerodha] }),
    codeStep: { broker: zerodha, link: "https://kite.zerodha.com/connect/login", opened: true },
  },
};

export const LoginFailed: Story = {
  name: "Login needed: login failed, two brokers",
  args: {
    state: brokerGateState({ ...base, tokens: [tokenRow("upstox"), tokenRow("fyers", { account_label: "desk" })] }),
    error: "Upstox did not accept the login. Try again; if it repeats, check your Upstox keys in Settings.",
  },
};

export const Connected: Story = {
  name: "Connected: the page renders",
  args: { state: brokerGateState({ ...base, tokens: [tokenRow("upstox", { needs_login: false, token_status: "VALID" })] }) },
};

export const Loading: Story = { name: "Checking", args: { state: brokerGateState({ ...base, tokens: null }) } };

export const CheckFailed: Story = { name: "Check failed", args: { state: brokerGateState({ ...base, tokens: null, failed: true }) } };

export const SignedOut: Story = { name: "Signed out", args: { state: brokerGateState({ ...base, signedIn: false, tokens: null }) } };

import type { WelcomeCard } from "../types";

/** MO2 part C: the welcome tour on the first sign-in - four cards, ending on "Connect your broker" (the Broker page). */
export const WELCOME: readonly WelcomeCard[] = [
  {
    id: "what",
    title: "Welcome to AiAlgoDesk",
    body: "AiAlgoDesk watches the market for the setups you choose, shows them as signals and lets you practise with paper trades before any real money.",
  },
  {
    id: "broker",
    title: "Connect your broker",
    body: "Your broker gives AiAlgoDesk live prices and charts. Pick it once on the Broker page; most brokers need one or two fields.",
  },
  {
    id: "paper",
    title: "Your paper bot",
    body: "Start a strategy in paper mode: it trades with pretend money, so you can see how it behaves without any risk.",
  },
  {
    id: "alerts",
    title: "Get alerts on your phone",
    body: "Turn on Telegram or app notifications in Settings, so a new signal reaches you even when AiAlgoDesk is closed.",
  },
];

/** The last card's button: the Broker page. */
export const WELCOME_CTA = { label: "Connect your broker", to: "/broker" } as const;

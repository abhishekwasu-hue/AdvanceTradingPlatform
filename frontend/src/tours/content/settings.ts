import type { PageTour } from "../types";

/** MO2 part C: the Settings page tips. Settings redesign B: each step points at its row on the Settings index. */
export const SETTINGS_TOUR: PageTour = {
  id: "settings",
  steps: [
    { id: "appearance", target: "settings-appearance", title: "Make it comfortable",
      body: "Open Appearance for light or dark, and a larger text size. It follows you to every device." },
    { id: "alerts", target: "settings-alerts", title: "Alerts on your phone",
      body: "Open Alerts to connect Telegram or turn on app notifications, so new signals reach you." },
    { id: "ai", target: "settings-ai", title: "Your own AI key",
      body: "An AI key lets AiAlgoDesk use an AI service on your account. Open AI: the guide there shows how to get a free one in about two minutes." },
    { id: "help", target: "settings-help", title: "Tips and help",
      body: "Open Help & tips to show all tips again, or turn them off." },
  ],
};

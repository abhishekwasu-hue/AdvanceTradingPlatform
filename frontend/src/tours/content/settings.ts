import type { PageTour } from "../types";

/** MO2 part C: the Settings page tips. */
export const SETTINGS_TOUR: PageTour = {
  id: "settings",
  steps: [
    { id: "appearance", target: "settings-appearance", title: "Make it comfortable",
      body: "Light or dark, and a larger text size. It follows you to every device." },
    { id: "alerts", target: "settings-alerts", title: "Alerts on your phone",
      body: "Connect Telegram or turn on app notifications, so new signals reach you." },
    { id: "ai", target: "settings-ai", title: "Your own AI key",
      body: "An AI key lets AiAlgoDesk use an AI service on your account. The guide here shows how to get a free one in about two minutes." },
    { id: "help", target: "settings-help", title: "Tips and help",
      body: "Show all tips again, or turn them off, from here." },
  ],
};

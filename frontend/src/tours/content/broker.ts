import type { PageTour } from "../types";

/** MO2 part C: the Broker page tips. */
export const BROKER_TOUR: PageTour = {
  id: "broker",
  steps: [
    { id: "what", target: "broker-connect", title: "Why connect a broker",
      body: "ATP reads live prices and charts through your broker. Trading starts in paper mode, with pretend money." },
    { id: "pick", target: "broker-tiles", title: "Pick your broker",
      body: "Tap your broker's logo. Most need one or two fields; some open their own login page and bring you back here." },
    { id: "result", target: "broker-advanced", title: "When it is connected",
      body: "Your broker then shows at the top of this page with a green dot. Some brokers need a fresh login each morning: a banner tells you when. Advanced is rarely needed." },
  ],
};

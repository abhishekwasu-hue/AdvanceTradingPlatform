import type { PageTour } from "../types";

/** MO2 part C: the Option chain page tips. */
export const OPTION_CHAIN_TOUR: PageTour = {
  id: "option-chain",
  steps: [
    { id: "pick", target: "oc-pickers", title: "Pick a symbol and expiry",
      body: "Choose the index or stock and the expiry date. The page opens on the nearest expiry by itself." },
    { id: "summary", target: "oc-summary", title: "The key figures",
      body: "The put-call ratio, max pain and the days to expiry, worked out from the whole chain. Tap the small i for what each one means." },
    { id: "chain", target: "oc-chain", title: "Read the chain",
      body: "Calls are on the left and puts on the right. The strike nearest the price is in the middle; tap a row to see all its figures." },
  ],
};

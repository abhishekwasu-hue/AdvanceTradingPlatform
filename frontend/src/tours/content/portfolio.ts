import type { PageTour } from "../types";

/** MO2 part C: the My portfolio page tips. */
export const PORTFOLIO_TOUR: PageTour = {
  id: "portfolio",
  steps: [
    { id: "summary", target: "portfolio-summary", title: "Where your money is",
      body: "How much sits in open positions, their open profit or loss, what you have booked and what is at risk at your stops." },
    { id: "allocation", target: "portfolio-allocation", title: "Symbol by symbol",
      body: "Each symbol's share of your capital. A long bar means much of your money sits in one place." },
    { id: "booked", target: "portfolio-booked", title: "Booked over time",
      body: "Your booked profit and loss after each closed trade, so you see the trend rather than one day." },
  ],
};

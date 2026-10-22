import type { PageTour } from "../types";

/** MO2 part C: the Charts page tips. */
export const CHARTS_TOUR: PageTour = {
  id: "charts",
  steps: [
    { id: "symbol", target: "charts-toolbar", title: "Pick a symbol",
      body: "Type a symbol in the search box at the start of this bar, for example an index or a stock, to open its chart." },
    { id: "timeframe", target: "charts-timeframes", title: "Choose a timeframe",
      body: "Each button sets how long one candle lasts, from one minute to one day." },
    { id: "chart", target: "charts-chart", title: "Read the chart",
      body: "Each candle shows where the price opened, how far it moved and where it closed in that time. Drag left for older history." },
    { id: "indicators", target: "charts-indicators", title: "Add indicators",
      body: "Turn on volume, RSI or a moving average. Your choice is kept for every chart." },
  ],
};

// M76 MP-3 (OPEN_QUESTIONS M76Q-7 default): the chart the Copilot's workspace has open in this tab - its instrument key and
// its reading timeframe - so a ticket opened beside it reads the same timeframe for its structure lines. Nothing else
// reads it; the workspace clears it when it closes. No network, no storage: one value in this tab's memory.

export interface OpenChart { instrument: string; tf: string }

let current: OpenChart | null = null;

export function setOpenChart(chart: OpenChart | null): void { current = chart ? { ...chart } : null; }

export function openChart(): OpenChart | null { return current ? { ...current } : null; }

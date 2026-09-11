# Frontend Console

A Vite + React + TypeScript + Tailwind single-page app that drives the FastAPI backend in
`../backend`. It is a control panel/console rather than the full 19-tab dashboard the platform
brief describes — every page here is wired to a real backend endpoint and computes real
results; nothing is mocked. See [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) for what
exists today versus what's still to be built (auth, persistence, live market data, the
remaining dashboard tabs).

## Pages

- **Dashboard** — live backend health, strategy/broker counts, engine overview
- **Strategy Library** — the seven inbuilt strategies and their default parameters
- **Signals** — generate a signal from any strategy and see the full weighted "why this trade"
  score breakdown (`POST /api/strategies/{id}/signal/enrich`), or paper-execute it
- **Backtesting** — run a strategy over sample OHLCV bars, see metrics, an equity curve, and
  the trade log
- **Option Chain** — analyze a sample option chain (PCR, Max Pain, ATM/ITM/OTM, OI activity,
  bias) with a toggle to see bullish/bearish/conflicting outcomes

## No live market data yet

No broker is authenticated (credential-accepting endpoints don't exist until the encrypted
secrets-storage phase — see `app/brokers/` in the backend), so every page generates a
deterministic, clearly-labeled sample OHLCV series client-side (`src/utils/sampleData.ts`)
instead of pretending to show live prices. The backend computation on top of that sample data
is real: the same code path that will run against live broker data once one is connected.

## Running locally

```bash
# terminal 1 - backend
cd ../backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# terminal 2 - frontend
cd frontend
npm install
npm run dev
# open http://localhost:5173 - /api/* is proxied to the backend on :8000
```

## Why Vite instead of Next.js

The brief's preferred stack is React/Next.js. This console is a client-only SPA control panel
with no server-rendering or routing needs yet, so Vite + React + TypeScript is a faster-moving
and equally standard substitute for that shape of app — swapping to Next.js is straightforward
later if SSR, file-based routing, or API routes become useful once auth and persistence land.

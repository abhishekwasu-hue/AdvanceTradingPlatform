# Roadmap status (MASTER SPEC v1 + v1.1 / v1.2 / v1.3)

Updated with docs/WORK_LOG.md (at least every 2 hours while work runs). Merge only on the owner's "Merge".
Order: A -> C1+C4 -> D -> B (ADR -> build) -> E -> C2-C6 -> F -> G1-G2 -> H -> I -> G3-G6 -> J -> K.
Indian PAPER go-live on Hostinger comes before the global work (v1.2); crypto paper (CoinDCX spot) after it (v1.3).

Last update: 2026-10-10 22:35 IST.

| Part | What | Status | PR | % |
|---|---|---|---|---|
| A | Review fixes (13 items) | done, CI green, waiting for the owner's merge | #82 | 100 |
| Go-live | Hostinger KVM 2: bootstrap / deploy / rollback blocks, runbook, 8 GB budget | done, CI green, waiting for the owner's merge; the owner runs the blocks when the server IP exists | #84 | 100 |
| Go-live | H-1 Redis guard (lock loss fails closed, memory warning) + H-2 Cloudflare R2 off-site | done, CI green (stacked on #84) | #85 | 100 |
| C1 | HTF lookahead: closed bars only | done | #83 | 100 |
| C3 | Speed (indicator prefix cache), benchmark, CI guard | done; portfolio backtest still to come | #83 | 75 |
| C4 | Reproducibility fingerprint | done | #83 | 100 |
| C2 | Pluggable fill / slippage / latency / margin / pricing / settlement models | next (design note first) | - | 0 |
| C5 | Trial ledger, deflated Sharpe | after C2 | - | 0 |
| C6 | Distributed runs | after C5 | - | 0 |
| D | SEBI retail-algo (D1-D8) | design PR next after C (spec order: D before B) | - | 0 |
| B | Market data lake (ADR-0013, + ADR-0016 provider seams, ADR-0017 global-first) | not started | - | 0 |
| E | Execution core and scale (ADR-0014) | not started | - | 0 |
| F | Portfolio risk | not started | - | 0 |
| G | Trade repo engine port (ADR-0015) | not started | - | 0 |
| H | AI research loop / MCP | not started | - | 0 |
| I | Options analytics | not started | - | 0 |
| J | UX / real-time | not started | - | 0 |
| K | Community / enterprise | not started | - | 0 |
| v1.3 | Crypto paper: CoinDCX spot -> Binance/Kraken -> IBKR | after the Hostinger PAPER go-live | - | 0 |

Owner decisions pending: none open in docs/OPEN_QUESTIONS.md (H-1, H-2 answered 2026-10-10). On the owner: R2 bucket +
token in the server .env; the server IP for the Hostinger blocks.

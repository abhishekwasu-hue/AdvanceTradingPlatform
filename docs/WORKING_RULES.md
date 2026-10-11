# Working rules (Abhi, standing - apply to every item)

These are in addition to MASTER SPEC §0 (don't stop, OPEN_QUESTIONS with a provisional default, WORK_LOG pushed at least
every 2 hours, draft PRs only, merge only on Abhi's "Merge", no LIVE / VPS / secrets).

## 1. Multitasking is required
- Anything that runs longer than about 2 minutes goes to the background (`run_in_background`, or
  `nohup ... > /tmp/<name>.log 2>&1 &`).
- The next module or item starts at once, in its own `git worktree` (`git worktree add ../wt-<name> -b claude/<name> origin/main`,
  or on that item's branch).
- Read a log only when the result is ready (`tail -n 40 /tmp/<name>.log`). Never poll it in a loop.
- Only one heavy job (full suite, build) runs at a time. Reading code and writing specs, tests and docs are not heavy;
  neither are small targeted tests. Those always continue.
- Waiting for CI is not a reason to stop: start the next item.

## 2. Self-review is required before every push of a finished item
- (a) Re-read the whole diff (`git diff origin/<branch>...HEAD`) as a sceptical reviewer. Check:
  - the logic against the spec;
  - look-ahead and known_at;
  - bar-index off-by-one;
  - timezone (IST);
  - direction and sign (bull put vs bear call, high vs low);
  - silent exceptions;
  - hard-coded dates or figures;
  - tests that can never fail.
  For ATP, also check:
  - tenant isolation;
  - blocking calls inside async code;
  - exits never blocked (ADR-0004);
  - no AI action without approval (ADR-0006);
  - LIVE flags off by default;
  - reversible migrations.
- (b) Run the new tests and the module's existing tests. For ATP, also run ruff and mypy on the files touched.
- (c) Put a 5-10 line "self-review" note in the commit message or PR body: what was checked, what was fixed.
- (d) Anything for Abhi goes to OPEN_QUESTIONS.
- After the push, if time allows, do a second independent pass on the same diff (fresh eyes or a reviewer subagent).
  Anything found goes in a follow-up commit.

## Queue notes from Abhi (order)
- **IBKR adapter (MASTER v1.2 §2)** comes after the Indian PAPER go-live and crypto Phase 1. The design PR comes first
  (ADR-0018 IBKR transports). The spec is in `docs/specs/IBKR_ADAPTER_SPEC.md`.
- **Crypto adapters (v1.3 §3)**, in this order:
  1. CoinDCX (exists);
  2. Binance spot;
  3. Bybit / KuCoin / OKX (perps and options only in allowed jurisdictions);
  4. CoinSwitch PRO / ZebPay.

  Kraken comes later.
  - The exchange registry carries `fiu_registered` (config, with its source and date).
  - An India resident gets only fiu_registered exchanges, and spot only.
  - Tax: a 30 % + 1 % TDS report seam (P2).

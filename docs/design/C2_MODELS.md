# Part C2 - Pluggable execution models for backtests: design note

Spec: MASTER SPEC v1 §2 (C2). Status: **PR 1 done** (protocols, defaults, golden test; fill + slippage wired). It is stacked on the realism PR (#83: C1 HTF closed bars, C3 prefix
cache, C4 reproducibility fingerprint). The models below are added in small PRs. The default model of each kind
reproduces today's numbers exactly, so no existing backtest result changes until someone picks a different model.

## Today (what the engine hard-wires)
| Concern | Where it lives now | Behaviour |
|---|---|---|
| Fill | `app/backtest/engine.py` + `app/trading/exit_logic.determine_exit_price` | Entry at the signal's entry; exits at the stop, target or close of the bar that touches them; gap-through fills at the open (P0.6) |
| Slippage | `PaperBroker._slip` | a fixed `slippage_pct` (0.02 %) against the trade |
| Latency | none | A signal fills on the bar it is generated on |
| Margin | none for single-leg; `options_engine` sizes by max loss | No margin check, no margin interest |
| Options pricing | `options_engine` uses recorded chains (`chain_recorder`) | No model price when a strike is missing |
| Settlement | `options_engine`: intrinsic value on expiry; equity intraday square-off | Hard-coded |
| Costs | `app/execution/india_costs.py` (statutory rates by trade date) | **Stays where it is**: costs are not a C2 model |

## Design
`app/backtest/models/` holds one small protocol per concern. Each protocol has a registry of named implementations,
and a `ModelSet` dataclass is passed to `run_backtest` / `run_option_backtest`. The default `ModelSet()` equals
today's behaviour.

| Protocol | Method | Default | First alternatives |
|---|---|---|---|
| `FillModel` | `entry(signal, bar, next_bar) -> price \| None`, `exit(trade, bar, levels) -> (price, reason) \| None` | `touch` (today) | `next_open` (fill at the next bar's open), `limit_queue` (fills only if the price trades through the level by a tick) |
| `SlippageModel` | `apply(price, side, bar, qty) -> price` | `fixed_pct` (0.02 %) | `spread_half` (half of the recorded spread), `volume_share` (impact grows with qty / bar volume) |
| `LatencyModel` | `delay(signal_time) -> timedelta` | `zero` | `fixed_ms` (from config), `sampled` (seeded distribution; the seed goes into the C4 fingerprint) |
| `MarginModel` | `required(position, price) -> money`, `check(equity, required) -> bool` | `none` | `span_like` (percentage of notional per instrument kind, from config), `options_max_loss` (today's options sizing) |
| `OptionsPricingModel` | `price(contract, spot, iv, t) -> premium` | `recorded_only` (today) | `black76` (the pricing in `app/option_chain/greeks.py`) for strikes missing from the chain, with every model price flagged in the result |
| `SettlementModel` | `settle(position, expiry_bar) -> (price, charges_basis)` | `intrinsic` (options) / `intraday_squareoff` (equity) | `physical` (stock-settled F&O: delivery STT basis) |

Rules that apply across all six:
- **The choice is part of the result.** The model names and their parameters go into `BacktestResult.reproducibility`
  (C4 `config_hash`). A run using non-default models can never be confused with a default run.
- **No look-ahead.** A model only receives the current bar and, for `next_open`, the next bar's *open*. It is never
  given a later bar's high, low or close. The C1 guard tests are extended to every model.
- **Parameters come from config or the request, never from code.** Any number a model needs (latency ms, margin %,
  impact coefficient) has a typed setting with a default. Thresholds are never tuned against backtest results (§14).
- **Costs stay in `india_costs.py`.** A model can change the price or the timing of a fill. The charges on that fill
  are still computed by `india_costs` for the trade's date.

## Order of work (each PR at most about 800 lines)
1. This note, plus the `models/` package with the six protocols and the defaults wired through `run_backtest` and
   `run_option_backtest`. Golden test: every default-model result is byte-identical to before (C4 `result_hash`).
2. `next_open` fill, `spread_half` / `volume_share` slippage, `fixed_ms` latency. API and UI selectors. Tests.
3. `span_like` margin, with a margin-call exit when equity falls below the requirement. `black76` pricing for missing
   strikes, with every model price flagged.
4. `physical` settlement for stock F&O. `sampled` latency with the seed in the fingerprint.

## Test plan
- Golden: for each of the 13 strategies, the default models give a `result_hash` equal to the hash on the realism
  branch.
- Per model, crafted bars:
  - `next_open` never fills on the signal bar;
  - a gap through the stop fills at the open;
  - `volume_share` slippage grows with quantity;
  - a latency that crosses a bar boundary moves the fill to the next bar;
  - `span_like` refuses an entry that the equity cannot margin;
  - `black76` matches a reference value within tolerance;
  - `intrinsic` and `physical` settlement charge STT on the right basis.
- Look-ahead guard: every model gets a crafted next bar whose high, low and close would change the answer. The answer
  must not change.
- Fingerprint: changing any model or parameter changes `config_hash`; the same models and seed give the same
  `result_hash`.

## Open questions
- C2-1: which slippage model should the UI pre-select once alternatives exist? Provisional: the default stays
  `fixed_pct`, and the user has to choose another model explicitly. No silent change.
- C2-2: what default margin percentage per instrument kind for `span_like`? Provisional: config values from the
  broker's published SPAN + exposure for the index's near month, refreshed by hand. They are not fetched live until
  part B has the data.

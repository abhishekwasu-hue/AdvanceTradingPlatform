# ADR-0021: Screener engine - one typed AST (ScreenQL) over Factor/Filter/Classifier primitives, server data only

**Date:** 2026-10-11 · **Status:** provisional (spec 0.6: the recommended option, until the owner says otherwise) · **Part:** S0

## Context
- **Today's Market Scanner** (`app/scanner`) ANDs three lists: DSL conditions, structure filters and option filters. It
  runs on candles that the browser posts.
- **The Screener spec v2** (`docs/specs/ATP_SCREENER_SPEC.md`) asks for much more:
  - 50+ screens across technical, F&O, fundamental, ownership, breadth and crypto;
  - mixed timeframes and candle offsets;
  - rankings and point-in-time history;
  - alerts;
  - a visual builder, a query language and a natural-language box that stay in sync.
- **Constraints from earlier records:**
  - the lake is the source of history (ADR-0013);
  - AI acts only through approved proposals (ADR-0006);
  - approval evidence comes from server data (H-C1 a).

## Options
| | Keep extending the scanner's three lists | Embed a general language (SQL over DuckDB, or Python expressions) | A small typed DSL with its own AST (ScreenQL) |
|---|---|---|---|
| Expressiveness | AND only; no offsets, no mixed timeframes | everything | what the field registry exposes; grows by adding fields and functions |
| Look-ahead / cost control | none | hard (arbitrary SQL can join the future) | the validator owns it: offsets, timeframes, `as_of`, cost cap |
| Builder ⇄ text ⇄ Copilot parity | n/a | text only | one AST; three views; round-trip tests |
| Safety (multi-tenant, user text) | safe | SQL injection / sandboxing work | no execution of user code; only registry functions |

## Decision (provisional)
1. **ScreenQL.**
   - `screenql/1` is a typed, versioned grammar: fields; registry functions; offsets `[n]`; timeframes `@1m..@1M`;
     ALL/ANY/NOT; between/in; parameters `$name`; saved custom formulas.
   - It is parsed by a hand-written recursive-descent parser into a JSON-serialisable AST.
   - The visual builder and the Copilot produce the same AST. There is no user-supplied code and no raw SQL.
2. **Primitives.**
   - Factor (number), Filter (bool) and Classifier (category).
   - Each is registered with its inputs, window length, timeframe, `as_of` rule and cost.
   - The registry is the only list of what a screen may use.
   - The existing scanner filters become the first registry entries, and `/api/scanner/run` is translated onto them
     (backward compatible; parity test).
3. **Validator before every run.**
   - It checks types and units, timeframe compatibility, look-ahead (no future offset; no higher-timeframe field before
     that bar closes) and a per-tenant cost cap.
   - A refused screen says why, with the position in the text.
4. **Executor modes.**
   - EOD batch: vectorised.
   - Bar-close incremental: finalised bars only.
   - Live: in-memory 1-minute aggregates from the tick stream.
   - Historical `as_of`.
   - In every mode, universe filters run first and expensive factors last.
5. **Data.**
   - The lake's `md_*` tables (as-of, versioned) are the truth.
   - DuckDB over the Parquet cold tier plus derived factor tables are a rebuildable compute cache.
   - Screens never run on client-posted candles. The scanner's `candles` field is deprecated, then removed, as in H-C1 a.
6. **Reproducibility.**
   - Each run stores the AST version, the universe snapshot id and the lake data version.
   - The result cache key is hash(AST, universe, as_of, timeframe).
7. **Wording.**
   - Results are "matches".
   - Server text passes the H-C1 c output filter.
   - A screen never proposes an order. The hand-off to a strategy is a monitor proposal (ADR-0006).

## Consequences
- **One new package, `app/screener/`:** grammar, AST, validator, registry, executor, cache.
- **Fields cost a little.** Every new field is a registry entry with tests (definition, as-of, fixture result).
- **Tables and dependency.** The first new tables are saved screens, runs and custom formulas (S1). DuckDB arrives with
  S2, as a wheel with no server.
- **Language growth.** Some power-user requests (arbitrary SQL) are out of scope by design. If the grammar grows past
  about 30 productions, a parser generator (Lark) is reconsidered (SCREENER.md SC-3).
- **Reversible.** The scanner API stays, and the new tables come with down-migrations.

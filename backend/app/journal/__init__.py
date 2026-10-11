"""Z6 / P1-e (ADR-0026): the discipline engine - journal auto-capture, behaviour tags, the weekly discipline score.

* `settings`  - every threshold and weight, with documented defaults (docs/DISCIPLINE_ENGINE.md); editable per trader.
* `capture`   - the hooks the trade pipeline calls (entry fill, monitor marks, stop changes, exit) and the rebuild from
                the trade row. Best effort: a failure here never touches the trade or the order.
* `detectors` - pure behaviour-tag rules over a list of journal trades.
* `score`     - the pure 0-100 weekly discipline score with its components.
* `service`   - the database glue (load a week, refresh tags, build the score).
* `routes`    - `/api/journal/*`, behind the `discipline_engine` flag (OFF by default).

Numbers only: nothing here recommends a trade, a security or an action.
"""
FLAG = "discipline_engine"

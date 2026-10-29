"""AUD-T6 (owner audit 28 Oct; BR-M3 similar-setup recall + BR-R1 "what the context said vs what happened").

* `service` - pure matching over the user's own stored trades, and the database reads (self-scoped, read-only).
* `routes`  - `/api/setup-recall/*`, behind `ui_v2` + `setup_recall` (404 while either is off).

Information only: nothing here places, changes or blocks an order, and nothing re-reads a chart.
"""
FLAG = "setup_recall"

"""Master Order 4 slice 3 (docs/owner-orders/ATP_SESSION_GROWTH_MASTER.md parts 3-5): the user's plan page with a usage
meter (`plan.py`), the "my discipline this week" share card (`share.py`) and, in the admin console, the activation
events (`app/admin/console.py` `activation`). Slice 4 (PART 2 items 3, 5, 9, 10, 11): the "ready-made dish" at the top of
Signals, My positions, the Journal, Charts and Find setups (`dishes.py`).

Behind `ui_v2` + `growth_v2` (OFF by default): with either off the routes answer 404 (X-Feature-Disabled names the flag)
and the pages show nothing new. Read-only: nothing here changes a plan, starts a trial, sends a reminder, shows a price
or touches an order. No migration: every number comes from rows that already exist - except AUD-G2.4's activation steps
(`activation.py`), recorded once per person per step in `activation_events`.
"""
FLAG = "growth_v2"
PAGE_FLAG = "ui_v2"

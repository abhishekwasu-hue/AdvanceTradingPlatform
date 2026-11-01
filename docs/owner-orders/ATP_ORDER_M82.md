# ATP ORDER M82 - Copilot "Today's read" card: the true state (owner order, 1 Nov, verbatim)

M82 (ATP) - Copilot "Today's read" card says "No market read yet ... needs a broker session" while the owner's broker is connected (Sunday, market closed).
Code: backend/app/ai/briefing.py UNKNOWN headline = no market-memory read stored; it never checks the broker or the last session.
R1 Find the root cause on production first (is any market-memory read stored? did the worker run on the last session? is the broker token valid today?) and report it in one line.
R2 Market closed: show the last session's read with its date ("Last session, Fri 30 Oct"), never "no read" when one exists.
R3 Exact reason, one line: none ever read / broker token expired - reconnect (in place) / worker did not run. Never "needs a broker session" when a valid session exists.
R4 "Read now" button on this card itself (in place, no navigation); disabled with the reason when no valid broker session.
No new rule, no defaults changed. Proof: before/after phone 390 + desktop, dark + light. Report in Marathi <= 25 lines.

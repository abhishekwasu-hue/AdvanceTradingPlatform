# ATP ORDER BRK-1 - Easy login + visible step-by-step guide for every broker (owner order, 2 Nov 2026, verbatim)

ATP order (owner, 2 Nov 2026): EASY LOGIN + VISIBLE STEP-BY-STEP GUIDE FOR EVERY BROKER. Self-review every diff before push. Log questions with a provisional default and keep working. All UI text in English.

A. Remove the Kotak Neo / Groww special gates. The owner never ordered them; the DECISIONS.md row "TM-3 / TM-5 / TM-6 ... no LIVE orders or prices for Kotak Neo and Groww until the lawyer and the brokers confirm in writing" is NOT an owner decision. Correct that row (mark TM-5/TM-6 as withdrawn by the owner, 2 Nov 2026).
   - app/brokers/gates.py: delete the kotak_neo and groww entries from GATES (both brokers behave exactly like the other adapters). Remove the module and its calls in kotak_neo.py / groww.py if nothing else needs it.
   - Remove "Practice only" / "Practice mode only" everywhere (catalog guide, tile badge, ConnectPanel, LiveOrdersClosed / MarketDataClosed texts). Remove requires_ui_v2 for their tiles.
   - Platform-wide rules stay as they are for ALL brokers equally: phase0_paper_only lock, live_trading flag, broker_data_terms.json (Kotak Neo and Groww follow the same data-terms rule as Fyers / Angel One).
   - Static-IP / market-order rules (SEBI, from 1 Apr 2026) apply to every broker: show them as the same one short info line on every broker's connect panel, not only on two.

B. Easiest official login for every broker (research each broker's CURRENT official API docs first; pick the method with the fewest user steps; record source + date in docs/brokers/EASIEST_LOGIN.md):
   - Upstox: keep pasted Analytics Token (one year). No Upstox OAuth flow.
   - TOTP brokers (Angel One, Shoonya, Kotak Neo, Groww): fully automatic daily login - a scheduled job renews the session before market open with the stored encrypted TOTP secret; no "Renew" click. Manual Renew stays as a fallback button.
   - Zerodha, Fyers: replace "paste the address you land on" with automatic capture - redirect to ATP's own callback, ATP exchanges the code; the user only signs in on the broker page.
   - Dhan: use the longest-validity / least-daily-effort method Dhan currently offers officially; if a daily token is still the only way, keep paste with one field.
   - CoinDCX: unchanged (API key + secret, no daily login).
   - Every broker: one clear status (Connected / Needs login / Not working), a reminder via Telegram and in-app before the session expires, and the smoke test's quote step must not fail because of a removed gate.

C. Visible step-by-step guide INSIDE each broker's connect panel (for a first-time user who has never made an API key):
   - Shown open by default on the first connect (not hidden behind "Where do I get this?"); on a later Renew it is collapsed. A beginner must be able to create the key/secret on the broker side using only this guide.
   - Numbered steps, one action per step, in the broker's own current menu names, e.g. "Open the Kotak Neo app > More > Trade API > Create application". Research each broker's CURRENT official flow (web + mobile where they differ); record source and date checked in docs/brokers/EASIEST_LOGIN.md and add a test that every catalog entry has a guide.
   - Each step that happens on the broker side has a direct link to that exact official page (developer console, API keys page, TOTP setup page), opening in a new tab.
   - Values the user must copy INTO the broker (Redirect URL, static IP if any) have a Copy button with the exact value.
   - Each input field in the panel says which guide step produces it ("From step 3").
   - Warn up front, in one line each, only where true: the broker charges for API access; a static IP must be registered; TOTP SECRET (the long key / QR text), not the 6-digit code; the secret is shown only once - save it.
   - Screenshot slots per step (HOW_TO_IMAGES): fill with real screenshots only from official public docs where licence allows; otherwise leave empty, never mock-ups.
   - Short plain English, bold key words, numbers first; must read cleanly at 390 px (no overflow, no cut-off text, good contrast in dark and light).
   - Keep the rest of the panel minimal: guide + only the needed fields + one Connect button.

D. Tests for A-C (update test_part1b_kotak_neo.py / test_part1b_groww.py which assert the gates), before/after screenshots of the Broker tiles and EVERY broker's connect panel with its guide open, at 390 px and 1280 px, dark and light. Draft PR -> CI green -> self-review -> merge (standing approval). VPS deploy stays the owner's.

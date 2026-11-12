# HANDOVER_NOW (12 Nov)

State: M96, LIVE-1, LIVE-2, M97, M98, M99, PHONE-1 and ORD-1 are all merged. main = c91e6bb7.

Deploy pending: the owner runs the deploy block for the main head. Migrations: b4d6f8a0c2e3 and b4d6f8a0c2e5.

Flags stay OFF until the owner turns them on: sd_pack, vsa_pack, wyckoff_pack, fib_ew_pack, pa_pack, risk_options, m99_primitives, intraday_pack.

Open items:
- M60 e2e race: diagnostics merged in #1035; root cause comes from the [m60-diag] lines of the next failing run.
- The owner's ✅ after checking the real site.

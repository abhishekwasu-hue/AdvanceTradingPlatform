"""SRV2 (owner 15 + 18 Nov, DECISIONS 18 Nov SRV2): the owner's Trade bot "15M SRv2", ported AS IS into the private package
(atp_private.srv2 + the copied Trade code). This package is ATP's side only: the loader (`loader.py`, the one module here that names
the private package), the owner's settings rows (`store.py`), the IO the copied code asks for - Upstox reads through ATP's broker
budget, the owner's Telegram chat, the market calendar (`io.py`) - and the worker job that calls the bot's schedule (`job.py`).

Owner-only (the bot reads the owner's own method): the flag `srv2_bot` (OFF by default) and the platform owner's organisation and
account. PAPER: the bot's positions live in its own store on the worker's data volume (SRV2_DATA_DIR); LIVE is not wired (the owner's
words first). Nothing here places an ATP order or touches ATP's positions or exits (ADR-0004 / ADR-0006)."""

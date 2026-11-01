# Seeding the market-data store from the owner's private data (M71 PL-7)

What it does: `atp.sh import-trade-data` checks out the owner's PRIVATE data repository on the host (read-only, its own
deploy key) and imports its market-data files into HIS private store (`md_tenant_candles` of the owner's organisation),
with one bookkeeping row per file (source path label, sha256, rows, first / last bar, gaps) and the data-use windows of
the private policy. Afterwards the paced broker backfill (M71 PL-1c) asks only for what is still missing. Code:
`backend/app/market_lake/seed_import.py` (import), `data_use.py` (sealed / contaminated windows), `seed_store.py` (reads).

Nothing of the private data is in this repository: no file, row, price, date list or screenshot. The import never runs
on `deploy`; it runs only when the owner runs the command.

## Settings (all in `/opt/atp/.env`, except the key file)

| Name | Default | What |
|---|---|---|
| `~/.ssh/trade_data_ro` (file) | - | The read-only deploy key of the private data repository (a NEW key, separate from the private package's `~/.ssh/atp_private_ro`). `ATP_TRADE_DATA_KEY` overrides the path. Used only through `GIT_SSH_COMMAND` with `IdentitiesOnly=yes`. |
| `ATP_TRADE_DATA_REPO` | - | The private data repository's ssh address (`git@github.com:<owner>/<repo>.git`). |
| `ATP_TRADE_DATA_REF` | `main` | The branch or tag to check out. |
| `ATP_TRADE_DATA_DIR` | `/opt/atp/trade-data` | The host checkout (git-ignored; only the market-data folders, blobs fetched on demand). Mounted read-only into a one-off backend container as `/trade-data`. |
| `HISTORY_OWNER_TENANT_ID` + `HISTORY_OWNER_TENANT_NAME` | already set (HB0) | The owner's organisation, confirmed by its name - the ONLY organisation the import writes (M71Q-2). |
| `TRADE_DATA_POLICY_SCOPE` | `NIFTY` | The underlying(s) a data-use table without its own scope applies to (comma list; `*` = all). |
| `HISTORY_BACKFILL_FO_VOLUME` | `withhold` | F&O bhavcopy volume: withheld = stored as missing (NULL) until its unit is settled; `as_printed` stores it. |

## Owner steps (once)

1. On GitHub, private data repository > Settings > Deploy keys > Add deploy key: paste the PUBLIC key printed by step 2,
   title "ATP host (read-only)", leave "Allow write access" OFF.
2. On the host, one block (as root; it creates the key for the `atp` user, adds the two settings, then runs the import
   detached and shows its log):

```sh
su - atp -c 'test -f ~/.ssh/trade_data_ro || ssh-keygen -t ed25519 -N "" -C atp-trade-data-ro -f ~/.ssh/trade_data_ro; cat ~/.ssh/trade_data_ro.pub'
# add the printed key on GitHub (step 1), then:
su - atp -c '/opt/atp/deploy/atp.sh env-set ATP_TRADE_DATA_REPO=git@github.com:<owner>/<private-data-repo>.git ATP_TRADE_DATA_REF=main'
su - atp -c "nohup sh -c '/opt/atp/deploy/atp.sh import-trade-data --check && /opt/atp/deploy/atp.sh import-trade-data' > /tmp/atp-seed.log 2>&1 &"
tail -n 15 /tmp/atp-seed.log
```

`env-set` checks both values (an ssh address of GitHub; a branch or tag name) and keeps every other line of `.env`.
A re-run is safe: a file already imported with the same sha256 is skipped; a changed file is read again (identical bars
skipped, a changed bar stored as the next version).

## What the command prints

The totals only (files done / skipped / refused, bars written, backfill windows marked complete or gap, policy windows).
The per-file detail (`--details` on the module) names paths inside the private checkout and belongs in the owner's
private report, not in a public issue or chat.

## Rules it keeps

- Read-only on the data: the checkout is mounted `:ro`; the importer never writes there.
- Owner-only: the owner's organisation (HB0's confirmation rule); refused (exit 2) when it is not configured.
- Store conventions: END-labelled UTC bars, closed bars only, missing volume stays missing (NULL), DH3 closing-auction
  flags, two different bars with one END refuse the file.
- Data policy (PL-7b): sealed and contaminated windows are refused by the optimiser, walk-forward and the AI research
  loop; charts, live reading, backtests shown to the user and screeners display them.
- Migration: revision `c5e7a9b1d3f6` (volume may be NULL in the private store; `data_use_windows`). Not rollback-safe
  for a code-only rollback once the import has run (`--restore-db` or the migration's downgrade).

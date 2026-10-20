# Public repository policy

Status: in force from 2026-10-20, when the owner (Abhi) made this repository public to restore the GitHub Actions
minutes. The code stays proprietary: see [`LICENSE`](../LICENSE) (all rights reserved; public visibility is not a
licence). Open owner decisions are in [`docs/OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md), section "Public repository
(20 Oct)" (PR-1 to PR-5).

## 1. The rule

**Everything committed is public**: every file, every commit message, every branch, every pull request and its
comments, every CI log, and the whole history, for every commit ever pushed. Deleting a file later does not
un-publish it; the history keeps it.

## 2. Never commit

- **Credentials of any kind:** API keys, broker API keys / secrets / TOTP seeds, access or refresh tokens, passwords,
  JWT signing keys, Fernet keys, `.env` files with values, private keys (`*.pem`, `*.key`), keystores (`*.jks`,
  `*.keystore`, `*.p12`, `*.pfx`, `*.pepk`), `google-services.json`, service-account JSON files.
- **Real market or trade data:** bars, ticks, option chains, bhavcopies, licensed vendor data, the owner's intraday
  history, backtest results on real data.
- **Golden or parity fixtures derived from real data**, and new golden / parity fixtures in general (section 5).
- **Abhi's charts** (approved charts, review and audit charts, screenshots of his charts), his **trade journal**,
  tradebooks, contract notes, P&L reports and broker exports.
- **Strategy-sensitive notes:** the KB, the Method Pack, the Chart Reader method notes, research notes, frozen model
  coefficients fitted on real data.
- **Identifiers:** broker account / client ids, Telegram bot tokens, chat ids and user ids, phone numbers, personal
  e-mail addresses, server IP addresses and host names that identify the production server.

Write placeholders instead: `<server-ip>` in docs, `atp.example.com` or a documentation address (`192.0.2.x`,
`198.51.100.x`, `203.0.113.x`) in tests, `owner@example.com` for an e-mail, a short fake chat id such as `987654`, a
value marked `FAKEFAKE` for a fake key.

## 3. Where those go instead

The private stores are the ones the Trade takeover plan defines ([`docs/design/TRADE_TAKEOVER.md`](design/TRADE_TAKEOVER.md),
sections 3 and 7, imported by `atp.sh import-trade` on the host, never into git):

| What | Where it lives |
|---|---|
| Secrets and settings | The server's `/opt/atp/.env` and `/opt/atp/secrets/` (mode 600); the GitHub secret store (repository or environment secrets) for CI; Abhi's password manager for backups. |
| Server IP, SSH details | Abhi's own notes. Docs say `<server-ip>` / `ssh atp@<your-server-ip>`. |
| Real bars and market data | The market-data lake on the host: the shared `md_candles` (daily) and the owner's private `md_tenant_candles` (intraday, FUT; HB0 / HB2 rules). |
| Golden fixtures, parity fixtures, the owner's approved charts | The private fixtures store on the host (TRADE_TAKEOVER section 3: tests read it through an environment variable; in public CI those tests are skipped with a visible reason). A manifest of paths and sha256 only may be committed. |
| Trade's records (PAPER journal, signals, Vision log, review and audit charts) | The private read-only archive on the host. |
| KB and Method Pack | Not in this public repository: either the repository goes private again or they live in a separate private package (open question PR-1, decided before the Chart Reader port starts). |

Each port also keeps a few small **synthetic** cases in git (made-up bars, mirror pairs) so the public CI always
exercises the code.

## 4. How the guards work

| Guard | When | What |
|---|---|---|
| `scripts/check_public_repo.py` | CI `lint` job on every push and pull request; pre-commit hook | Fails on the production server IP (matched by its sha256), any other public IPv4 literal outside the documentation ranges and its explicit allow-list, e-mail addresses outside example / test domains and its explicit allow-list, Telegram bot tokens and long chat ids, private key headers, secret file types, `.env` files with values, and new golden / parity / real-data fixture files. Findings are printed redacted. Tests: `backend/tests/test_public_repo_check.py`. |
| gitleaks, push range | CI `lint` job (`.github/workflows/ci.yml`) | Scans the commits of each push with `.gitleaks.toml`. |
| gitleaks, full history | `.github/workflows/secret-scan.yml`: every push to main, weekly (Monday) and on demand | Scans every commit of every branch and tag (`--log-opts=--all`, redacted). The release binary is pinned by version and sha256. |
| gitleaks, staged changes | pre-commit hook (`.pre-commit-config.yaml`, pinned to the same gitleaks release) | Stops a secret before the commit exists. Install: `pip install pre-commit && pre-commit install`. |

The allow-lists are explicit and narrow. `.gitleaks.toml` allows only exact fake values (each anchored, with a comment
saying where it is); `.gitleaksignore` lists the same known findings by exact fingerprint (commit, file, rule, line).
Adding an entry is a reviewed change, and a real secret never gets one.

## 5. Grandfathered fixtures (owner decision pending)

These files were committed before the repository went public. `scripts/check_public_repo.py` lists them in
`GRANDFATHERED_FIXTURES`, so they pass while every NEW file of the same kind fails. **Each is to move to private
storage or be replaced with synthetic data - owner decision pending (open question PR-2).** The list only shrinks: when
a file moves, its entry is removed (a test checks that every entry still exists). Their own notes say the inputs are
synthetic and seeded; the outputs still record Trade's or ATP's method results, so the owner decides.

| Files | What they are (from their own notes) |
|---|---|
| `backend/tests/fixtures/daily_trend/golden.json.gz`, `backend/tests/fixtures/level_gate/golden.json.gz`, `backend/tests/fixtures/options_builder/golden.json.gz`, `backend/tests/fixtures/strike_breach/golden.json.gz` | Trade's own functions run on synthetic, seeded inputs (`make_golden.py` next to each). The strike-breach file carries Trade's frozen NIFTY coefficients. |
| `backend/tests/fixtures/oi_regime/golden.json` | Trade's OI analysis results (`capture_from_trade.py`). |
| `backend/tests/fixtures/price_action/golden_trade_1e6c9ac_ch3b.json.gz` | Trade's price-action output (CH3b parity). |
| `backend/tests/fixtures/zones/golden_trade_eafc872.json.gz`, `backend/tests/fixtures/zones/golden_level_memory_eafc872.json.gz` | Trade's market-zones and level-memory output on the zones CSVs (see the folder's README). |
| `backend/tests/fixtures/zones/bn_daily.csv`, `market_5m.csv`, `session_15m.csv`, `walk_1m.csv` (same folder) | Synthetic, seeded bars copied from Trade's test generators (the folder's README). |
| `backend/tests/fixtures/z10a/structure_base_c5f6a2e.json.gz` | The market-structure baseline of the no-look-ahead harness. |
| `backend/tests/fixtures/charts/pa_layer_golden.json`, `backend/tests/fixtures/charts/study_parity.json`, `frontend/src/charting/__fixtures__/pa_layer_golden.json`, `frontend/src/charting/__fixtures__/study_parity.json` | ATP's chart price-action layer and study cases (the frontend copies are byte-identical). |
| `frontend/src/charting/__fixtures__/chart_types_golden.json.gz`, `frontend/src/utils/__fixtures__/indicator_parity.json` | Chart-type and indicator parity cases generated by the backend. |
| `backend/tests/golden/c2_default_models.json` | Result hashes of the default models on crafted bars. |
| `backend/tests/fixtures/universe/EQUITY_L.csv`, `SME_EQUITY_L.csv`, `eq_etfseclist.csv`, `fo_mktlots.csv`, `fo_secban.csv`, `ind_close_all_09102026.csv`, `ind_testindexlist.csv`, `symbolchange.csv`, `symbolchange_noheader.csv` (same folder) | Small made-up rows in NSE's file layouts, for the universe importer. |

**Vendored third-party code:** `docs/reference/amw-screener-v2.9/` is a copy of the AMW screener whose licence is
unknown (open question AMW-L1). It is now public as well; whether it is removed from the tree is an owner decision
(open question PR-3).

## 6. If something slips

1. **Rotate first.** Treat the value as known to everyone the moment it was pushed: revoke or rotate the key, token
   or password at its provider (broker developer console, BotFather for a Telegram bot, the cloud console, the
   server's `.env`), and re-deploy. For data or an identifier that cannot be rotated, tell the owner at once.
2. **Remove it from the tree** in a normal commit (placeholder or synthetic value), so the guards pass again.
3. **History is the owner's decision.** Rewriting history (to purge a file from every commit) needs a force push,
   invalidates every clone and fork, and does not remove copies others already made. Nobody rewrites history or
   force-pushes without the owner's explicit decision (open question PR-4).
4. **Record it** in `docs/OPEN_QUESTIONS.md` (what slipped, when, what was rotated), without repeating the value.

## 7. Commit metadata

Commit author names and e-mail addresses are public too. Future commits can use the GitHub `noreply` address
(open question PR-5).

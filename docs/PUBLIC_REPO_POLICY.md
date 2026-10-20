# Public repository policy

Status: in force from 2026-10-20, when the owner (Abhi) made this repository public to restore the GitHub Actions
minutes. The code stays proprietary: see [`LICENSE`](../LICENSE) (all rights reserved; public visibility is not a
licence). Open owner decisions are in [`docs/OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md), section "Public repository
(20 Oct)" (PR-1 to PR-6).

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
| `scripts/check_public_repo.py` | CI `lint` job on every push and pull request; pre-commit hook | Fails on the production server IP (matched by its sha256, dotted or with `-`, `_`, `/`, `:` or a space between the numbers), any other public IPv4 literal outside the documentation ranges and its explicit allow-list (a version after a known browser token such as `Chrome/` is not an address), e-mail addresses outside example / test domains and its explicit allow-list, Telegram bot tokens and long chat ids, private key headers, secret file types (keys, keystores, `*.ppk`, `keystore.properties`, `signing.properties`, `google-services.json`, `*service-account*.json`, `*credentials*.json`), `.env` files with values, `.npmrc` / `.pypirc` with a token or password, the KB / Method Pack / Chart Reader method by name (PR-1; by name only - a renamed copy is caught only in review), a private fixture listed in `backend/tests/private_fixtures_manifest.json` (section 5), new golden / parity / real-data fixture files, any data or compressed file under `backend/tests/`, `backend/evals/`, `frontend/test/`, `frontend/e2e/` or a `__fixtures__` folder, and any tabular or binary data file (csv, tsv, xlsx, parquet, feather, pkl, npy, npz, h5, arrow) anywhere, unless listed (section 5). Listed test data is pinned by sha256, so a listed file with new content fails too. Findings are printed redacted. Tests: `backend/tests/test_public_repo_check.py`. |
| gitleaks, push range | CI `lint` job (`.github/workflows/ci.yml`) | Scans the commits of each push with `.gitleaks.toml`. |
| gitleaks, full history | `.github/workflows/secret-scan.yml`: every push to any branch, weekly (Monday) and on demand | Job `gitleaks-full-history` (blocking): every commit of every branch and tag (`--log-opts=--all`), then the checked-out tree (`gitleaks dir`). Job `gitleaks-pull-request-refs` (report only): the pull request heads (`refs/pull/*`, which anyone can create from a fork and which never go away); a finding there is a warning and a job summary with counts and fingerprints, triaged as section 6 says. All redacted; the release binary is pinned by version and sha256. |
| gitleaks, staged changes | pre-commit hook (`.pre-commit-config.yaml`, pinned to the same gitleaks release) | Stops a secret before the commit exists. Install: `pip install pre-commit && pre-commit install`. |

**The IP hash is not secrecy.** A sha256 of an IPv4 address is reversible in seconds (there are only 2^32 addresses);
the hash only keeps the literal out of the tree. The address is in the history anyway (PR-4).

**What the guards do not see** (a person reviews these in every pull request): an IP address written as an integer, in
hex or split across lines; text inside binary files (images, archives, PDFs), UTF-16 files and files over 8 MB; and
the content of screenshots. **Screenshots and images under `docs/` get a human review** before they are committed:
no real account, balance, position, order, chart of Abhi's, broker id, e-mail address or server address in them.

The allow-lists are explicit and narrow. `.gitleaks.toml` allows only exact fake values (each anchored, with a comment
saying where it is); `.gitleaksignore` lists the same known findings by exact fingerprint (commit, file, rule, line).
Adding an entry is a reviewed change, and a real secret never gets one.

## 5. Test data: private fixtures, allowed, and adding new

Owner decision PR-2 (20 Oct): the synthetic fixtures are regenerated and stay in git, pinned; Trade's own outputs
(and the strike-breach file) moved to the private fixtures store; the public CI skips the tests that need them, with a
visible reason.

### The private fixtures store

Eight Trade-derived golden files are no longer in the tree. Their content stays in the history (owner decision PR-4:
no history rewrite), so nothing is lost; the private package repository decided under PR-1 will hold the store.

| File | What it is (from its own notes) | Tests that read it |
|---|---|---|
| `backend/tests/fixtures/daily_trend/golden.json.gz` | Trade's Daily trend functions on synthetic, seeded Daily series | `test_n1_l2_daily_trend.py` |
| `backend/tests/fixtures/level_gate/golden.json.gz` | Trade's level gate and method functions on synthetic 5-minute sessions | `test_n1_l1_level_gate.py`, `test_n1_x1_scan_then.py` (one check) |
| `backend/tests/fixtures/oi_regime/golden.json` | Trade's OI analysis results on seeded inputs | `test_o1_oi_regime.py` |
| `backend/tests/fixtures/options_builder/golden.json.gz` | Trade's strategy selection and payoff functions on synthetic chains | `test_p1a_options_builder.py` |
| `backend/tests/fixtures/strike_breach/golden.json.gz` | Trade's strike-breach model on a synthetic series, with the frozen NIFTY coefficients (PR-6) | `test_z5_p1d_strike_breach.py` |
| `backend/tests/fixtures/price_action/golden_trade_1e6c9ac_ch3b.json.gz` | Trade's price-action engines on the synthetic zones bars (CH3b) | `test_ch3b_trade_parity.py` |
| `backend/tests/fixtures/zones/golden_trade_eafc872.json.gz` | Trade's market zones on the synthetic zones bars | `test_zones_parity.py` |
| `backend/tests/fixtures/zones/golden_level_memory_eafc872.json.gz` | Trade's level memory on the synthetic zones bars | `test_zones_memory_parity.py` |

- **The manifest** `backend/tests/private_fixtures_manifest.json` lists each path with the sha256 of its bytes (what
  `sha256sum` prints) and the commit the export reads (`source_commit`, a `main` commit that has the files in its
  tree).
  It holds no data. It is pinned in `ALLOWED_TEST_DATA` like any test data file, so changing it is a reviewed change.
- **The store** is a directory named by the environment variable `ATP_PRIVATE_FIXTURES`; each file keeps its
  repository path under it, for example `$ATP_PRIVATE_FIXTURES/backend/tests/fixtures/zones/golden_trade_eafc872.json.gz`.
  That directory is the private package's `fixtures/` (private CI) or a directory on the host; parity fixtures built
  on licensed data stay on the host only (section 3).
- **Filling it:** from a full clone (not shallow), `python3 scripts/export_private_fixtures.py --to DIR` writes every
  manifest file from `source_commit` (or `--from-commit SHA`) into DIR and checks each sha256; `--list` is a dry run.
  DIR must be outside the repository. On the host, keep it read-only for the test runner.
- **The tests** read the files only through `backend/tests/private_fixtures.py`. Store not mounted
  (`ATP_PRIVATE_FIXTURES` unset): the test is skipped with the reason `private fixture store not mounted
  (ATP_PRIVATE_FIXTURES): <path> - runs on the host / private CI; see docs/PUBLIC_REPO_POLICY.md section 5` (the CI
  test steps run pytest with `-rs`, so every skip reason is in the log). The variable set but the directory or a file
  missing (a mistyped or empty store), or a file whose sha256 is not the manifest's: an error, never a skip, so the
  private job fails instead of passing green. Manifest paths must be plain relative paths (no absolute path, no
  `..`); the resolver, the export and the guard refuse others.
- **Public CI** runs everything else. The synthetic inputs of those goldens (Daily series, 5-minute sessions, option
  chains, the strike-breach series) are built in code by `synthetic_inputs.py` in each fixture folder, so the tests
  that need only inputs - no look-ahead, the scan-then runner, the strike-breach report, the options-builder unit
  tests - keep running there; a test that runs with the store checks the inputs built in code against the golden's.
- **The host or a private runner** runs the parity tests: `ATP_PRIVATE_FIXTURES=/path/to/store python -m pytest ...`
  from `backend/` (a private runner mounts the store read-only).
- **A Trade re-sync** writes a new golden into the store (each generator's notes give the command; `make_golden.py`,
  `capture_from_trade.py` and `python -m tests.<harness> --write` write to `$ATP_PRIVATE_FIXTURES/<path>`), and the
  new sha256 replaces the file's line in the manifest (and the manifest's pin) in a reviewed pull request.
- **The guard refuses** any manifest path that comes back into the tree, whatever the other lists say, and any file
  whose sha256 is one the manifest lists (a renamed copy). A missing manifest (while the resolver is in the tree) or an
  unreadable one is a finding too.

### Allowed test data

`scripts/check_public_repo.py` keeps two explicit lists:

- **`ALLOWED_TEST_DATA`** - synthetic or hand-made data (ATP's own goldens, seeded bars, made-up rows, eval sets,
  e2e API replies, the private fixtures manifest), each pinned by the sha256 of its content (CRLF read as LF). A
  deliberate regeneration updates the hash in the same PR, after a check that the file holds no real data.
- **`ALLOWED_PUBLIC_DATA`** - public data rebuilt by automation, allowed by path only (it changes on every refresh):
  `backend/app/instruments/data/nse_index_expiries.csv`, the NSE index expiry calendar rebuilt weekly from NSE's public
  F&O bhavcopies by `.github/workflows/nse-expiries.yml`.

Every allowed file is synthetic, seeded or hand-made (checked 2026-10-20); none holds real market data. Each one with a
generator in the repository was regenerated on 2026-10-20 and came out byte-identical to its pin, except the chart-type
golden, whose gzip bytes depended on the zlib build: it is now stored as plain JSON (`chart_types_golden.json`, same
content) and regenerates byte for byte.

| Files | What they are | Generator (regenerated byte-identical) |
|---|---|---|
| `backend/tests/fixtures/charts/pa_layer_golden.json`, `frontend/src/charting/__fixtures__/pa_layer_golden.json` | ATP's chart price-action layer on the synthetic 15m bars (the frontend copy is byte-identical) | `python -m tests.ch3_pa_cases --write` |
| `backend/tests/fixtures/charts/study_parity.json`, `frontend/src/charting/__fixtures__/study_parity.json` | ATP's study cases on the synthetic 15m bars | `python -m tests.ch6_study_cases --write` |
| `frontend/src/charting/__fixtures__/chart_types_golden.json` | Heikin Ashi, Renko and range bars of a seeded random walk | `UPDATE_GOLDEN=1 npx vitest run src/charting/chartTypes.test.ts` |
| `frontend/src/utils/__fixtures__/indicator_parity.json` | Backend indicator values on generated bars | `Z10_REGENERATE=1 python -m pytest tests/test_z10a_chart_parity.py` |
| `backend/tests/fixtures/zones/bn_daily.csv`, `market_5m.csv`, `session_15m.csv`, `walk_1m.csv` | Synthetic, seeded bars | `python tests/fixtures/zones/build_fixtures.py` |
| `backend/tests/fixtures/zones/chart_bands_cases.json`, `frontend/src/charting/__fixtures__/chart_bands_cases.json` | Chart band cases on the synthetic bars | `python -m tests.zones_chart_cases --write` |
| `backend/tests/golden/c2_default_models.json` | Result hashes of the pre-C2 backtest engine on crafted bars | none: recorded once from the earlier engine; `test_c2_models.py` recomputes every hash |
| `backend/tests/fixtures/z10a/structure_base_c5f6a2e.json.gz` | The market-structure baseline of the pre-Z10-a code on synthetic bars | none: written once by the code at `c5f6a2e`; `test_z10a_no_lookahead.py` recomputes it |
| `backend/tests/fixtures/universe/*.csv` (9 files) | Small made-up rows in NSE's file layouts, for the universe importer | none: hand-made |
| `backend/evals/baselines.json`, `backend/evals/sets/*.jsonl` | Eval sets and their measured baselines | none: hand-written sets; baselines measured and edited by hand |
| `frontend/e2e/fixtures/*.json` | Hand-written API replies for the Playwright tests | none: hand-made |
| `backend/tests/private_fixtures_manifest.json` | Paths and sha256 of the private fixtures, no data | none: edited in a reviewed change |

### Adding synthetic test data

1. Make sure it is synthetic or hand-made: generated bars with a fixed seed, made-up rows, never an export of real
   market data, a broker, Abhi's account or Trade's private data. Real data goes to the private fixtures store
   (section 3).
2. Run the guard. Its finding for the new file says that synthetic data is fine and prints the exact line to add, for
   example `"backend/tests/fixtures/x/cases.json": "<sha256>",`. The same line comes from
   `python3 scripts/check_public_repo.py --hash <path>`.
3. Add the line to `ALLOWED_TEST_DATA` in `scripts/check_public_repo.py`, in the same PR, with a short comment on
   what the data is. After a deliberate regeneration of a listed file, replace its line the same way (each
   regeneration site names the command).
4. A reviewer checks the data is synthetic before approving.

**Frozen model coefficients fitted on real data:** `backend/app/options_builder/strike_breach.py` (the `COEF` table,
about lines 46-55) holds Trade's strike-breach logistic coefficients, fitted on NIFTY daily data 2015-2021 and
calibrated 2022-2024. That is the banned category "frozen model coefficients fitted on real data"; it is listed here
pending the owner's decision (open question PR-6: move to the private store, refit on synthetic data, or accept as
public). The strike-breach golden file moved to the private store under PR-2; the coefficients in the code are not
part of that decision.

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

**A finding in a pull request head** (the report-only job `gitleaks-pull-request-refs` of `secret-scan.yml`, whose job
summary lists fingerprints only): look at the commit. If the value is **ours** (one of ATP's or Abhi's credentials),
rotate it first, as above. If it is **not ours** (a third party's string in a fork's pull request, a fake value),
add its exact fingerprint to `.gitleaksignore` with a comment saying what it is and which pull request it came from.
A pull request head cannot be deleted from GitHub by us, which is why that job reports instead of failing.

## 7. Commit metadata

Commit author names and e-mail addresses are public too. Future commits can use the GitHub `noreply` address
(open question PR-5).

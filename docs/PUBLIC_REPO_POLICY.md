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
| `scripts/check_public_repo.py` | CI `lint` job on every push and pull request; pre-commit hook | Fails on the production server IP (matched by its sha256, dotted or with `-`, `_`, `/`, `:` or a space between the numbers), any other public IPv4 literal outside the documentation ranges and its explicit allow-list (a version after a known browser token such as `Chrome/` is not an address), e-mail addresses outside example / test domains and its explicit allow-list, Telegram bot tokens and long chat ids, private key headers, secret file types (keys, keystores, `*.ppk`, `keystore.properties`, `signing.properties`, `google-services.json`, `*service-account*.json`, `*credentials*.json`), `.env` files with values, `.npmrc` / `.pypirc` with a token or password, the KB / Method Pack / Chart Reader method by name (PR-1; by name only - a renamed copy is caught only in review), new golden / parity / real-data fixture files, any data or compressed file under `backend/tests/`, `backend/evals/`, `frontend/test/`, `frontend/e2e/` or a `__fixtures__` folder, and any tabular or binary data file (csv, tsv, xlsx, parquet, feather, pkl, npy, npz, h5, arrow) anywhere, unless listed (section 5). Listed test data is pinned by sha256, so a listed file with new content fails too. Findings are printed redacted. Tests: `backend/tests/test_public_repo_check.py`. |
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

## 5. Test data: grandfathered, allowed, and adding new

`scripts/check_public_repo.py` keeps three explicit lists:

- **`GRANDFATHERED_FIXTURES`** - Trade's own outputs committed before the repository went public (8 files; each needs
  a Trade checkout or export to regenerate), each pinned by sha256.
  **Each is to move to private storage or be replaced with synthetic data - owner decision pending (open question
  PR-2).** They never change (a Trade re-sync writes to the private fixtures store) and the list only shrinks: when a
  file moves, its entry is removed (a test checks that every entry still exists).
- **`ALLOWED_TEST_DATA`** - synthetic or hand-made data (ATP's own goldens, seeded bars, made-up rows, eval sets,
  e2e API replies), each pinned by sha256. A deliberate regeneration updates the hash in the same PR, after a check
  that the file holds no real data.
- **`ALLOWED_PUBLIC_DATA`** - public data rebuilt by automation, allowed by path only (it changes on every refresh):
  `backend/app/instruments/data/nse_index_expiries.csv`, the NSE index expiry calendar rebuilt weekly from NSE's public
  F&O bhavcopies by `.github/workflows/nse-expiries.yml`.

Every input below is synthetic or seeded, per each file's own generator notes (checked 2026-10-20); none holds real
market data. The grandfathered ones still record Trade's method results, so the owner decides.

| List | Files | What they are (from their own notes) |
|---|---|---|
| grandfathered | `backend/tests/fixtures/daily_trend/golden.json.gz`, `backend/tests/fixtures/level_gate/golden.json.gz`, `backend/tests/fixtures/options_builder/golden.json.gz`, `backend/tests/fixtures/strike_breach/golden.json.gz` | Trade's own functions run on a Trade export with synthetic, seeded inputs (`make_golden.py` next to each). The strike-breach file carries the frozen NIFTY coefficients (PR-6). |
| grandfathered | `backend/tests/fixtures/oi_regime/golden.json` | Trade's OI analysis results on seeded inputs (`capture_from_trade.py`). |
| grandfathered | `backend/tests/fixtures/price_action/golden_trade_1e6c9ac_ch3b.json.gz` | Trade's price-action output on the synthetic zones bars (CH3b parity, `tests/ch3b_golden.py`). |
| grandfathered | `backend/tests/fixtures/zones/golden_trade_eafc872.json.gz`, `backend/tests/fixtures/zones/golden_level_memory_eafc872.json.gz` | Trade's market-zones and level-memory output on the synthetic zones bars (see the folder's README). |
| allowed | `backend/tests/fixtures/charts/pa_layer_golden.json`, `backend/tests/fixtures/charts/study_parity.json`, `frontend/src/charting/__fixtures__/pa_layer_golden.json`, `frontend/src/charting/__fixtures__/study_parity.json` | ATP's chart price-action layer and study cases on the synthetic 15m bars (the frontend copies are byte-identical). |
| allowed | `frontend/src/charting/__fixtures__/chart_types_golden.json.gz`, `frontend/src/utils/__fixtures__/indicator_parity.json` | Chart-type and indicator parity cases on generated bars. |
| allowed | `backend/tests/golden/c2_default_models.json`, `backend/tests/fixtures/z10a/structure_base_c5f6a2e.json.gz` | Model result hashes on crafted bars; the market-structure baseline on synthetic bars. |
| allowed | `backend/tests/fixtures/zones/bn_daily.csv`, `market_5m.csv`, `session_15m.csv`, `walk_1m.csv`, `chart_bands_cases.json` (same folder) and `frontend/src/charting/__fixtures__/chart_bands_cases.json` | Synthetic, seeded bars (`build_fixtures.py`) and hand-made chart band cases. |
| allowed | `backend/tests/fixtures/universe/*.csv` (9 files) | Small made-up rows in NSE's file layouts, for the universe importer. |
| allowed | `backend/evals/baselines.json`, `backend/evals/sets/*.jsonl`, `frontend/e2e/fixtures/*.json` | Eval sets and baselines; hand-written API replies for the Playwright tests. |

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
public).

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

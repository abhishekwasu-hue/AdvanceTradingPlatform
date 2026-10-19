# AMW AI Custom Stock Screener™

A price-action intelligence and stock-screening platform for NSE equities: data validation, market-structure analysis,
six rule-based strategy engines, transparent confluence scoring, a custom **Strategy Lab** (eight authoring modes),
backtesting, paper trading, alerts, a REST API and a Streamlit dashboard.

> **Analysis only.** The platform never places broker orders. Scores are rule-based confluence measures, **not**
> probabilities of success. Yahoo Finance data is delayed/historical, not real-time. Live 1-minute data comes only from
> each user's own FYERS account and is shown only to that user.

| Area | Status |
|---|---|
| Dashboard (Home + 15 pages), REST API (`/api/v1`) incl. WebSocket bar stream, SQLite/PostgreSQL | Released |
| Strategy engines: Breakout, Pullback, Reversal, Trend Continuation, Intraday, Swing | Released |
| Strategy Lab — **Python**, **Pine Script (subset)**, **Visual No-Code Builder**, **SQL**, **Lua** | Released — work on Windows without extra installs |
| Strategy Lab — **JavaScript**, **TypeScript** (needs Node.js 22 + TypeScript 6.0.2), **R** (needs R 4.3) | Released — included in the Docker image; optional installs on Windows |
| Backtesting, paper trading, alerts (in-app; per-user Telegram via the official AMW bot or your own bot; email) | Released |
| Reports (PDF / XLSX / CSV / JSON / PNG) from the canonical signal record; scheduled scans and reports; plan entitlements | Released |
| AI Chart Analyst, AI strategy drafting, Dual Review — **Bring Your Own Key** (OpenAI and/or Anthropic) | Released — each user pays their own provider; AMW uses no company key |
| **Scanner Builder**: templates, natural-language builder (no AI key needed), versioned scanners, point-in-time replay, version comparison | Released (docs/SCANNER_SPEC.md) |
| Paper **forward tests** of every qualified signal, durable job history with restart recovery, report retention per plan | Released |
| **Live 1-minute data from your own FYERS account**: background collector (completed bars only), WebSocket stream, scans on broker data | Released — verified against a mocked FYERS API; do one live login/trading day before production (docs/LIVE_DATA_FYERS.md) |

See [ARCHITECTURE.md](ARCHITECTURE.md), [docs/STRATEGY_LAB.md](docs/STRATEGY_LAB.md), [docs/STRATEGY_RULES.md](docs/STRATEGY_RULES.md),
[docs/API.md](docs/API.md), [docs/SECURITY.md](docs/SECURITY.md), [docs/OPERATIONS.md](docs/OPERATIONS.md),
[docs/BACKTESTING.md](docs/BACKTESTING.md), [docs/AI_BYOK.md](docs/AI_BYOK.md), [docs/REPORTS_NOTIFICATIONS.md](docs/REPORTS_NOTIFICATIONS.md),
[docs/LIVE_DATA_FYERS.md](docs/LIVE_DATA_FYERS.md), [docs/SCANNER_SPEC.md](docs/SCANNER_SPEC.md),
[docs/TEST_REPORT.md](docs/TEST_REPORT.md), and the autonomous-session records in [docs/autonomous/](docs/autonomous/FINAL_REPORT.md).

> **AI API usage is billed separately by your selected AI provider.** You must supply your own API key and maintain your
> provider account's billing and usage limits. AI API charges are not included in your AMW subscription. Using multiple
> providers may generate separate charges.

---

## 1. Windows 10/11 quick start

**Requirement:** Python **3.11** from <https://www.python.org/downloads/windows/>. In the installer tick
**“Add python.exe to PATH”**. Open a *new* Command Prompt afterwards and check `py -3.11 --version` (or `python --version`).

1. Extract the ZIP and open the folder that contains `Home.py`.
2. Double-click **`setup_windows.bat`** (one time; creates `.venv` and installs dependencies).
3. Double-click **`start_screener.bat`** → the browser opens <http://localhost:8501>.
4. Optional: **`run_tests.bat`** runs the automated tests; **`start_api.bat`** starts the REST API on <http://127.0.0.1:8000/docs>.

Manual commands (PowerShell):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
$env:AMW_ALLOW_UNSANDBOXED = "1"       # local trusted mode for your own Python strategies (see below)
python -m streamlit run Home.py
```

Command Prompt: use `.venv\Scripts\activate.bat` and `set AMW_ALLOW_UNSANDBOXED=1`.

**Code strategies on Windows.** Windows has no OS-level sandbox (network/mount namespaces). `start_screener.bat`
therefore enables *local trusted mode*: your own strategy code (Python, SQL, Lua, JavaScript, TypeScript, R) runs in a
separate, restricted process (language-level allow-lists, no file/network APIs, empty environment, timeout) — appropriate
for a single user running their own code, **not** for running other people's code. Set `AMW_ALLOW_UNSANDBOXED=0` to
disable it. Pine Script and the Visual builder never execute user code and need no sandbox. For fully sandboxed
execution use Docker (section 3).

**Optional runtimes on Windows:** JavaScript/TypeScript need Node.js 22 (<https://nodejs.org>) and
`npm install -g typescript@6.0.2`; R strategies need R 4.3 (<https://cran.r-project.org>). Restart the app after
installing; the Strategy Lab capability table (and `python scripts\check_runtimes.py`) shows what can execute.
Operators can restrict modes with `AMW_LAB_LANGUAGES=python,pine,visual` (comma list).

### Troubleshooting

| Problem | Fix |
|---|---|
| `'py' is not recognized` | Install Python 3.11 with “Add to PATH”, open a new window; or use `python` instead of `py -3.11` |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `ModuleNotFoundError` | Activate `.venv` (prompt shows `(.venv)`), then `pip install -r requirements-dev.txt` |
| Port 8501 busy | `python -m streamlit run Home.py --server.port 8502` |
| “FALLBACK large-cap list” | NSE blocks scripted downloads at times. Download `ind_nifty500list.csv` (or 50/200) from niftyindices.com, put it in `data/`, click **List refresh** |
| Many `UNAVAILABLE` rows | Yahoo rate limit — lower *Batch size*, raise *Pause* in Settings, retry later |
| Python strategy says “sandbox unavailable” | Start with `start_screener.bat` (sets local trusted mode) or use Docker |
| Logs | `amw_screener.log` in the project folder |

### Updating safely
Stop the app (`Ctrl+C`), keep a copy of `data/` (database + your NSE CSVs) and any `.env`, extract the new version,
copy `data/` and `.env` back, run `setup_windows.bat`, then `run_tests.bat` before starting. Database migrations run
automatically and are idempotent.

---

## 2. Linux / macOS (development)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m streamlit run Home.py          # dashboard  :8501
python api_server.py                      # REST API   :8000  (docs /docs)
python -m pytest -v                       # tests (run as root on Linux to exercise the sandboxed tests)
```

On Linux the Strategy Lab sandbox (network namespace + mount namespace + uid 65534 + rlimits) is used automatically
when the process runs as root; it is verified by a self-test at start-up and shown on the Strategy Lab page.
Install the app and its virtualenv in a world-readable location such as `/opt/amw` — **not** under `/tmp`, `/var/tmp`
(replaced by a private tmpfs inside the sandbox) or `/root` (not traversable by the sandbox user). The self-test reports
the exact reason and code execution stays disabled until it passes.

---

## 3. Production deployment (Docker Compose, single server)

```bash
cp .env.example .env            # set POSTGRES_PASSWORD and AMW_SECRET_KEY (encrypts users' AI keys); never commit .env
docker compose up -d --build    # db (PostgreSQL 16), app (:8501), api (:8000)
docker compose exec api python -m src.api.manage create-user admin admin   # prints an API token once
curl http://localhost:8000/health/ready
```

Put a TLS-terminating reverse proxy (e.g. Caddy/Nginx) in front of ports 8501/8000 and restrict them to it.
The `app`/`api` containers need `CAP_SYS_ADMIN` for the Strategy Lab sandbox; without it the sandbox self-test fails
and code execution is disabled (fail-closed). Backups, upgrades, rollback and runbooks: [docs/OPERATIONS.md](docs/OPERATIONS.md).

---

## 4. Using the product

| Page | Purpose |
|---|---|
| Market Overview | Benchmark regime (rules + evidence), breadth/sectors from the last scan, data freshness, provider health |
| Advanced Screener | Universe (Nifty 50/200/500, NSE list, custom, watchlists), strategies, background scan with cancel/resume, filters, presets, CSV |
| Strategy Workbench | Built-in strategy parameters, preview old vs new rules, versioned configurations |
| Price Action Workbench | Chart with swings, BOS/CHoCH, zones, S/R, patterns, signal levels; MTF matrix; evidence; CSV |
| AI Chart Analyst | Evidence-grounded report; optional AI explanation with your own key (rejected if it cites numbers not in the evidence); Dual Review; AI strategy drafting |
| Report Center | Generate/download PDF, XLSX, CSV, JSON, PNG reports for stored signals; plan limits and usage |
| Scanner Builder | Templates by category, natural-language scanner with interpretation preview, JSON editing, save/version/duplicate, run or replay as of a past moment, compare two versions |
| Data Health & Admin | Active data source and fallback, provider and feed status, data quality of the last scan, users and plans (admin), audit log |
| Live Data (FYERS) | Connect your own FYERS app, daily login, start/stop the background 1-minute feed, live status, stored bars and chart, choose FYERS as your scan data source |
| AI Provider Setup & Usage | Add/test/replace/delete your own OpenAI/Anthropic keys, provider selection and fallback policy, spending safeguards, usage dashboard |
| Signal Intelligence | Ranked setups with passed/failed conditions, score breakdown, lifecycle history |
| Backtesting & Analytics | Equity/drawdown, IS/OOS, walk-forward, sensitivity, costs |
| Watchlist & Alerts | Watchlists, alert rules, cooldowns, delivery history, Telegram linking (official or own bot), email, quiet hours, schedules |
| Paper Trading & Journal | Simulated trades, risk sizing from configured equity, rule adherence; automatic forward tests of qualified signals |
| Settings & System Health | Risk/costs/labels, data/session/holidays, DB/provider/cache/jobs health, audit log |
| **Strategy Lab** | Build screeners visually or in Pine Script, Python, SQL, JavaScript, TypeScript, Lua or R → Validate → Run → Publish → Scan / Backtest |

Strategy Lab quick start: *New strategy* → mode **Pine Script** → template **EMA trend (v5)** → *Create draft* →
*Validate* → *Run* on a few symbols → *Publish version* → *Start scan* or *Backtest*. Details in
[docs/STRATEGY_LAB.md](docs/STRATEGY_LAB.md).

---

## 5. Data sources and limits

* **yfinance (Yahoo Finance)** — delayed/historical, research use. History: 5m/15m/30m ≈ 60 days, 1H ≈ 730 days
  (1 year requested), 1D/1W multi-year. Prices are unadjusted; jumps > 35 % are flagged as possible corporate actions.
* **NSE constituent CSVs** — official lists; when the download is blocked a clearly labelled *FALLBACK* large-cap list is used.
* **Fixture provider** (`AMW_PROVIDER=fixture`) — deterministic SIMULATED data for tests only; labelled `simulated` everywhere.
* **FYERS (your own account)** — completed 1-minute candles collected in the background (up to 100 symbols per user),
  streamed over WebSocket, and optionally used for your scans (daily/intraday history). Needs a daily FYERS login; the
  data is shown only to the account owner. Details, limits and vendor options: [docs/LIVE_DATA_FYERS.md](docs/LIVE_DATA_FYERS.md).
* A generic broker/vendor adapter template also exists (`BrokerProviderTemplate`).

Missing data is never filled or fabricated; insufficient/invalid data produces typed statuses
(`INSUFFICIENT`, `STALE`, `INVALID`, `UNAVAILABLE`) and can never produce a qualified signal.

## 6. Tests

```bash
python -m pytest -v                                   # 293 tests: 291 + 2 PostgreSQL-only (see docs/TEST_REPORT.md)
AMW_TEST_PG_URL=postgresql+psycopg://... python -m pytest -k postgres   # storage contract on PostgreSQL
ruff check .
```

All tests use deterministic synthetic fixtures and need no network.

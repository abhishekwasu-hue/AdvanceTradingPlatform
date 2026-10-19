# ADR-0027: Strategy Lab - user-authored strategy code runs only in an isolated runner (amends ADR-0005)

**Date:** 2026-10 · **Status:** proposed (AMW-18; owner approval of the AMW port plan, `docs/design/AMW_PORT.md` section
3) · **Amends:** ADR-0005 · **Threat model:** `docs/design/STRATEGY_LAB_THREAT_MODEL.md`

## Context
- The owner wants AMW's Strategy Lab, with all eight authoring modes:
  - visual (no-code) and a Pine Script subset;
  - six code modes: Python, SQL, Lua, JavaScript, TypeScript and R.
- ADR-0005 bounds ATP on purpose: one declarative DSL, "no arbitrary code, so no sandbox is needed". Two of the eight
  modes fit inside that rule. The six code modes do not: they run code that any signed-up user wrote.
- AMW's own sandbox (`src/lab/sandbox.py`) cannot be ported as it is. The port plan (section 3.4) lists twelve gaps,
  among them:
  - it needs root, `CAP_SYS_ADMIN` and `apparmor:unconfined` on the web application's containers;
  - it has no PID namespace and no cgroup;
  - all runs share one uid;
  - output is buffered without a bound;
  - there is an unsandboxed mode.
- ATP's production host is one 2 vCPU / 8 GB KVM (`docs/OPERATIONS.md` 1.2b). The trading worker, the exits (ADR-0004)
  and the alerts run on it. Container limits already sum to about 6.6 GB.

## Decision
1. **Host modes stay inside ADR-0005.**
   - Visual and Pine-subset strategies compile to ScreenQL (`screenql/1`, ADR-0021) plus the `lab-envelope/1` strategy
     envelope (sides, stop, target, parameters).
   - The host's ScreenQL runtime evaluates them on closed bars. No user code runs.
   - Pine is translated, never executed. A construct outside the subset is refused with a line-numbered diagnostic.
2. **Code modes run only in a separate runner container** (`lab-runner`, compose profile `lab`, not started unless the
   profile runs). The container has:
   - no network (`network_mode: none`); the only channel is a Unix domain socket shared with the dispatcher;
   - no secrets: no `.env`, no database credentials, no tenant, broker or BYOK keys; the environment holds only the
     socket path;
   - no database access;
   - a read-only root filesystem and a small `/tmp` tmpfs;
   - a non-root user, `cap_drop: [ALL]`, `no-new-privileges`, and `pids_limit`, `mem_limit` (`MEM_LAB`) and `cpus`
     limits;
   - gVisor (`runtime: runsc`) as the preferred runtime (AMW-Q3). Without gVisor, a seccomp profile for this container
     only.
   Neither `privileged` nor `CAP_SYS_ADMIN` is used on any container. The backend, worker and frontend containers gain
   no privilege, no package and no mount. There is no unsandboxed mode on any platform.
3. **Each run gets its own jail inside the runner** (nsjail or bubblewrap, pinned):
   - its own namespaces: user, PID (with a fresh `/proc`), mount, network, IPC and UTS;
   - a distinct unprivileged uid per concurrent slot, so two runs never share a uid;
   - a root built only from read-only binds of the runtime it needs, plus a private tmpfs work directory;
   - its own cgroup v2: `memory.max`, `memory.swap.max = 0`, `pids.max`, `cpu.max`;
   - a seccomp-bpf allow-list;
   - a wall-clock timeout and a CPU-time limit. On timeout or cancel the runner writes `cgroup.kill`, which ends the
     whole process tree, escaped children included;
   - capped output: stdout and stderr are read as a stream with a hard byte cap. On overflow the run is killed and
     nothing beyond the cap is buffered.
   Language guards (restricted builtins, `vm` realms, SQLite authorizer, Lua and R curated environments) stay as defence
   in depth only. They are never the boundary.
4. **The host builds every input; inputs hold completed bars only.**
   - The dispatcher in the research worker (AMW-Q7) builds each context on the host. Sources are the lake or the
     requester's own session (BT1b).
   - Bars are cut at the decision bar, with higher timeframes cut per `backend/app/backtest/windows.py` (Z10-a).
   - A context never carries a forming bar, account data, keys, or another user's data.
   - Contexts enter the runtime as JSON data, never as generated source code.
5. **Output is a strict JSON contract that the host validates.**
   - Each result is one framed message per call, size-capped before parsing.
   - Parsing refuses duplicate keys, excessive nesting and unknown fields.
   - The schema is checked; numbers must be finite; the stop must sit on the correct side of the close; strings are
     bounded.
   - The result must match the symbols and the run the host sent. The host sets every timestamp and identity.
   - Anything else fails the run with a sanitised error. Logs are capped, shown only to the owner, and rendered as
     plain text.
6. **One flag per code-mode family, each off by default.**
   - The flags are `strategy_lab` (page and core) and `strategy_lab_host` (visual and Pine), plus one flag per code
     family: `strategy_lab_python`, `strategy_lab_sql_lua`, `strategy_lab_js` (JavaScript and TypeScript) and
     `strategy_lab_r`.
   - A family is the set of modes that share one runner image and worker.
   - All of them are in `DEFAULT_OFF_FLAGS`. Turning a family flag off stops new runs and cancels queued runs of that
     family at once.
7. **The flag switch enforces gate G-LAB.** `set_flag` refuses to turn on a code-family flag unless all of these hold:
   - `strategy_lab` is on;
   - the runner's latest self-test is verified and fresh;
   - a security-review approval for that family is recorded (platform control `lab_security_review`: family, reviewer,
     date, commit, image digest, seccomp profile hash, worker script hash). A SUPER_ADMIN with MFA writes it, and the
     write is audit-logged.
   The approval is void as soon as the runner image digest, the seccomp profile or the worker script differs from the
   recorded one. Unlike the `legal_pages` guard, this refusal has **no override argument**. The evidence the review
   needs is the G-LAB checklist in the threat model.
8. **User code never places orders** (ADR-0006, PAPER only).
   - Code-mode results are research output: they are shown, backtested and reported.
   - They never create an `execution_core` intent, a Scan Then action, an auto-PAPER entry, a proposal or an alert
     rule (AMW-Q9). A test enforces this.
   - A code-mode version cannot be deployed. The Copilot does not write code-mode source (C-3).
   - Host-mode versions are ScreenQL, so they may become screens and alert rules like any screen. They still reach
     PAPER only through the existing approval paths.

## Consequences
- **Image size.** The backend image does not change: no Node, TypeScript, R, lupa or sqlglot. Each family has its own
  runner image, pulled only when its profile runs. Approximate sizes, to be measured in AMW-21:
  - `atp-lab-py` (Python, SQL, Lua): 150-200 MB;
  - `atp-lab-node` (JavaScript, TypeScript): 250-300 MB;
  - `atp-lab-r`: 350-450 MB;
  - gVisor's `runsc` on the host: about 50 MB.
  The total is under 1 GB of the 100 GB disk. Every image is pinned by digest and carries an SBOM. Each change to an
  image needs a re-review.
- **Memory on the 8 GB KVM.**
  - Dispatching adds no process: the research worker drains `lab_runs`.
  - One runner with `MEM_LAB=512m` and concurrency 1 covers the Python, SQL, Lua and Node families. It lowers the free
    headroom from about 1.4 GB to about 0.9 GB.
- **CPU.** The runner's `cpus` limit and each run's `cpu.max` keep Lab work off the trading worker's share of the 2
  vCPU. A per-plan daily budget of CPU seconds (`lab_cpu_seconds_per_day`, AMW-1) is enforced in the database, not in
  process memory.
- **R comes last.**
  - An R run needs about 512-768 MB. With `MEM_LAB=768m`, headroom falls to about 0.6 GB, below the comfortable
    margin.
  - R is therefore the last family (AMW-25). On this host it runs alone at concurrency 1, and only after measurement;
    otherwise it waits for a larger host (AMW-Q6).
- **Code modes arrive late.** None can be switched on before AMW-21 (runner and self-test) and the G-LAB review. The
  host modes (AMW-20) do not wait for it.
- **Operations cost.**
  - The probe suite runs at start, every hour and after each image change.
  - A failed or stale self-test turns every code mode "unavailable" on the next request (fail closed) and sends one
    operator notification.
  - The kill path is: turn the family flag off, then `docker compose --profile lab stop`. Trading, exits and alerts do
    not depend on the runner.
- **Accepted residual risk.**
  - Kernel bugs reachable through gVisor or the seccomp allow-list remain.
  - So do timing side channels between runs on a shared CPU.
  - A compromised runner can forge its own self-test. CI therefore runs the same probe suite against the published
    image digests.
  The threat model lists the rest.

## What ADR-0005 still guarantees
- Strategies that trade are declarative only. Builder strategies, backtests of deployable strategies, PAPER and LIVE
  deployments, and the worker all use `CustomStrategyConfig` through `BaseStrategy.build_signal()`, so exit parity
  holds.
- The AI generator emits the DSL (and, later, ScreenQL or the Pine subset), never code.
- No user-authored code runs in the backend, worker, research worker, API or frontend process. The only place it runs
  is a `lab-runner` jail.
- Host-mode Lab strategies, like DSL strategies, are fully inspectable and explainable. A code-mode result is
  explained only by the rules list its author's code returns, and the page labels it as user-supplied.
- Versions stay immutable: DSL `strategy_versions`, and `lab_versions` with source and compiled SHA-256 hashes.
- ADR-0005's consequence "no sandbox is needed" stays true for everything above. It is narrowed only for the Lab's code
  modes, which run in the runner.

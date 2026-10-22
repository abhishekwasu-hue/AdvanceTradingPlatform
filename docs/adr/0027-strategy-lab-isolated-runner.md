# ADR-0027: Strategy Lab - user-authored strategy code runs only in an isolated runner (amends ADR-0005)

**Date:** 2026-10 · **Status:** accepted (AMW-18, PR #264; approved by the owner on 2026-10-22, see "Owner approval"
below) · **Amends:** ADR-0005 · **Extends:** ADR-0006 · **Threat model:** `docs/design/STRATEGY_LAB_THREAT_MODEL.md`

## Owner approval (2026-10-22)
- The owner (Abhi) approved this ADR and `docs/design/STRATEGY_LAB_THREAT_MODEL.md` as the design of the Strategy Lab
  (owner order 11b, handover pack). This is the evidence for gate item G-1.
- The approval is of the design only. It turns nothing on: gate G-LAB (threat model section 9, items G-1..G-19) still
  gates every code-mode flag (`strategy_lab_python`, `strategy_lab_sql_lua`, `strategy_lab_js`, `strategy_lab_r`),
  per family, through `set_flag` and the run-time re-check (decisions 8-9). Host modes (decision 1) need no runner.
- Owner order 11e (same date) overrides the port plan's C-3: the AI may write code for every Lab mode, including
  Python. AI-written code is untrusted exactly like user code (the same validator, the same runner, the same limits;
  never published, scanned or run live automatically). The line "The AI generator emits the DSL ..., never code" below
  is read with that change; the full wording follows with AMW-19 / AMW-20.

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
  and the alerts run on it. Container limits already sum to about 6.6 GB, before the research worker.
- The research worker (`research-worker`), which the port plan picked to dispatch Lab runs, holds `DATABASE_URL`,
  `JWT_SECRET_KEY` and `SECRETS_ENCRYPTION_KEY`. It decrypts a user's broker token to build contexts from their own
  session (BT1b). It must never parse anything a runner writes.

## Decision
1. **Host modes stay inside ADR-0005.**
   - Visual and Pine-subset strategies compile to ScreenQL (`screenql/1`, ADR-0021) plus the `lab-envelope/1` strategy
     envelope (sides, stop, target, parameters).
   - The host's ScreenQL runtime evaluates them on closed bars. No user code runs.
   - Pine is translated, never executed. A construct outside the subset is refused with a line-numbered diagnostic.
2. **Code modes run only in a separate runner container, under gVisor.**
   - Each code family has its own runner container (`lab-runner-py`, `lab-runner-node`, `lab-runner-r`), under its own
     compose profile. A runner is started only when its family flag is to be on. When no family flag is on, no
     runner and no gateway run.
   - **gVisor (`runtime: runsc`) is required** for every runner on the shared trading host. Without it, code modes
     are "unavailable".
   - The only other way to run code modes is to move the runners to a separate host that holds no secrets and runs
     no trading process. That design needs its own review.
   - A plain runc runner on the shared host is not a simple fallback on Ubuntu 24.04:
     - `kernel.apparmor_restrict_unprivileged_userns` blocks the unprivileged user namespaces the jail needs;
     - Docker's `docker-default` AppArmor profile denies `mount`, so the jail cannot build its root;
     - the container's cgroup filesystem is mounted read-only, so the jail cannot create per-run cgroups.
     It is allowed only as an **owner-signed exception**. The exception needs a custom AppArmor profile, cgroup
     delegation and a stated sysctl posture. All three are added to the G-LAB evidence (G-2, G-4) and bound into the
     approval.
   - Every runner has:
     - no network (`network_mode: none`);
     - no secrets: no `.env`, no database credentials, no tenant, broker or BYOK keys;
     - no database access;
     - a read-only root filesystem and a small `/tmp` tmpfs;
     - one non-root uid, `cap_drop: [ALL]`, `no-new-privileges`, and no setuid binaries;
     - limits: `pids_limit`, `mem_limit`, `cpus`, a low `cpu_shares`, a high `oom_score_adj`, and the repository's
       `x-logging` caps.
   - Neither `privileged` nor `CAP_SYS_ADMIN` is used on any container. The backend, worker and frontend containers
     gain no privilege, no package and no mount. There is no unsandboxed mode on any platform.
   - A runner container is **recycled** (stopped and started fresh) after every N runs, and after any run that ended
     abnormally (timeout, kill, OOM, output cap, contract failure). No interpreter or worker is pooled or reused
     across runs.
   - **Recycling does not rely on the runner.** The threat recycling exists for is a compromised supervisor, which
     will not exit when asked. So:
     - the gateway enforces it by **refusing service**, keyed on a **start record written by the host** (container id
       and start time), like the deploy record, which the runner cannot forge. The gateway accepts exactly one runner
       session per start record, used up on first connect. After N runs, or after an abnormal end, it serves no
       further call on that session, and the family's code modes are "unavailable" until a fresh start record and a
       new connection appear. A runner that refuses to exit causes a Lab outage, never continued exposure. (A
       `SO_PEERCRED` pid is not usable as the key: the gateway's own PID namespace cannot see the runsc sandbox
       process, so the kernel reports pid 0.)
     - a trusted host-side actor restarts the container. The host agent in `deploy/atp.sh` first stops the old
       container (`docker stop`, then `kill`), so the sandbox dies whether or not its supervisor cooperates, and only
       then writes the new start record, so a dead sandbox cannot race the new one. `restart: unless-stopped` covers a
       supervisor that exits on its own;
     - the gateway itself has **no Docker socket and no Docker API access**. The Docker socket in a container that
       parses hostile frames would mean root on the host.
3. **Each run gets a fresh jail and a fresh process** inside the runner (nsjail or bubblewrap, pinned):
   - its own namespaces: user, PID (with a fresh `/proc`), mount, network, IPC and UTS;
   - **one uid.** The jail runs as the runner's single non-root uid, mapped to an unprivileged id inside its user
     namespace. There are no per-slot uids: distinct uids would need `CAP_SETUID`/`CAP_SETGID` or a setuid
     `newuidmap`, and the runner keeps neither.
   - What keeps the jail away from the supervisor is therefore not a uid but:
     - the PID namespace: the supervisor is invisible and cannot be signalled;
     - the mount namespace: the supervisor's files and socket are absent;
     - the read-only root: the supervisor's code is root-owned and immutable;
     - seccomp: no `ptrace` and no `process_vm_*`.
     A namespace escape lands directly on the supervisor's uid, with no second uid barrier. ADR-0027 accepts this
     because only one run executes at a time and the container is recycled.
   - a root built only from read-only binds of the runtime it needs, plus a private tmpfs work directory;
   - per-run cgroup v2 limits (`memory.max`, `memory.swap.max = 0`, `pids.max`, `cpu.max`) with `cgroup.kill`.
     AMW-21 must first prove that nested cgroups work inside runsc. If they do not, there are two fallbacks: the
     death of the PID namespace's init kills the whole tree, and with concurrency 1 the container's own limits are
     the per-run limits.
   - a seccomp-bpf filter that **denies by default**. Only the calls the runtime needs are allowed. The threat model
     lists the calls that are tested to be denied.
   - a wall-clock timeout and a CPU-time limit; the whole process tree ends at the deadline;
   - capped output: stdout and stderr are read as a stream with a hard byte cap. On overflow the run is killed and
     nothing beyond the cap is buffered.
   - determinism: a host-derived seed for every RNG, a fixed `PYTHONHASHSEED`, a clock fixed by the host, coarse
     timers, and no `SharedArrayBuffer` or worker threads. The seed and the image digest are recorded with the run so
     it can be reproduced.
   - no caches outside the run: compile and byte-code caches (`NODE_COMPILE_CACHE`, Python byte code, R lazy-load
     and byte-compile caches, TypeScript output) are off or live only in the run's tmpfs.
   Language guards (restricted builtins, `vm` realms, SQLite authorizer, Lua and R curated environments) stay as defence
   in depth only. They are never the boundary.
4. **Least privilege on the host side: a separate Lab gateway handles every runner frame.**
   - The research worker claims `lab_runs`. It builds each context from the lake, or from the requester's own session
     (BT1b, the only place a broker token is decrypted). It writes the context to a bounded `lab_run_inputs` row.
     **It never talks to a runner and never parses runner output.**
   - A separate `lab-gateway` container handles everything that touches a runner:
     - it listens on the Unix socket that the runners connect to;
     - it sends requests, parses frames, validates the contract, writes `lab_run_results` and charges the budget.
   - The gateway has no `JWT_SECRET_KEY` and no `SECRETS_ENCRYPTION_KEY`. It is on the internal network only, to
     reach Postgres. Its database role can touch only the `lab_*` tables.
   - **Socket roles.** The gateway listens; each runner mounts the socket directory read-only and connects. The
     gateway checks `SO_PEERCRED` and refuses symlinks. Only the gateway issues requests: an unsolicited frame from a
     runner closes the connection and fails the run, so a runner can never use the gateway as a confused deputy.
5. **The host builds every input; inputs hold completed bars only.**
   - Contexts come from the lake or the requester's own session (BT1b).
   - Bars are cut at the decision bar, with higher timeframes cut per `backend/app/backtest/windows.py` (Z10-a).
   - A context never carries a forming bar, account data, keys, or another user's data.
   - Contexts enter the runtime as JSON data, never as generated source code.
   - A host-side context cache is per user whenever the source is the user's own session.
6. **Output is a strict JSON contract that the gateway validates.**
   - Each result is one framed message per call, size-capped before parsing.
   - Parsing refuses duplicate keys, excessive nesting, integers over 4,300 digits and unknown fields.
   - The schema is checked; numbers must be finite; the stop must sit on the correct side of the close; strings are
     bounded.
   - The result must match the symbols and the run the host sent. The host sets every timestamp and identity.
   - Anything else fails the run with a sanitised error.
   - Logs are capped and shown only to the owner, rendered as plain text. In operator logs and notifications they
     are never relayed unescaped: newlines, ANSI sequences and bidi controls are escaped.
7. **Fair use is budgeted in slot seconds and enforced in the database.**
   - Global concurrency is 1 across all runners, held by one database lease. Claiming is round-robin across users.
   - Queued runs are capped per user, per tenant and globally.
   - Each user and each tenant has a daily budget of slot seconds per plan (`lab_cpu_seconds_per_day`, AMW-1, counted
     as slot seconds).
   - Every runner call is charged, validate and TypeScript compile included. The charge is the larger of the
     gateway's own measured wall time and the runner's report.
   - The research worker alternates between AI research studies and Lab context builds, so neither starves the
     other.
8. **One flag per code-mode family, each off by default.**
   - The flags are `strategy_lab` (page and core) and `strategy_lab_host` (visual and Pine), plus one flag per code
     family: `strategy_lab_python`, `strategy_lab_sql_lua`, `strategy_lab_js` (JavaScript and TypeScript) and
     `strategy_lab_r`.
   - A family is the set of modes that share one runner image and worker.
   - All of them are registered constants in `FEATURE_FLAGS` and `DEFAULT_OFF_FLAGS`. This matters because
     `flag_enabled` treats an unknown name as on.
   - Turning a family flag off stops new runs and cancels queued runs of that family at once.
9. **Gate G-LAB is enforced at the switch and before every run.**
   - **At the switch.** `set_flag` refuses to turn a code-family flag on, whether globally or for any tenant (a
     non-empty `tenants` list), unless all of these hold:
     - `strategy_lab` is on;
     - the self-test is verified and fresh;
     - a valid approval is recorded for that family.
     There is no override argument.
   - **The approval** (platform control `lab_security_review`) is written by a SUPER_ADMIN with MFA and audit-logged.
     It binds:
     - the family and the commit;
     - the runner image digest;
     - the runtime kind and the runsc version;
     - the jail configuration hash and the worker script hash;
     - the fixed module set;
     - the hash of the runner's compose service block;
     - the AppArmor profile hash, for a runc exception only.
     It **expires after 90 days**.
   - **Two-person rule.** The principal who writes the approval cannot be the one who turns the flag on.
   - **The trusted record.** The live values the approval is compared with come from a deploy-time record:
     `deploy/atp.sh` writes it with `docker inspect` and `runsc --version`. **A runner never self-reports anything
     the gate trusts.**
   - **Before every run.** The research worker at claim time, and the gateway before each call, refuse the run
     (fail closed) when any of these holds:
     - the approval is missing, mismatched or expired;
     - the self-test is stale;
     - the deploy record is missing;
     - `strategy_lab` is off.
     This also covers a flag turned on by a hand-edited control row.
10. **User code never trades; this extends ADR-0006 to user-authored code.**
    - Code-mode results are research output: they are shown, backtested and reported.
    - They never create an `execution_core` intent, a Scan Then action, an auto-PAPER entry, a proposal or an alert
      rule (AMW-Q9). A test enforces this.
    - A code-mode version cannot be deployed.
    - Code-mode strategies cannot be shared, listed in the marketplace or forked across users.
    - The Copilot does not write code-mode source (port plan C-3). Results are not shown to the Copilot. If that
      changes, they enter as delimited, untrusted data: they are never instructions, never grounding facts, and
      never a trigger for a tool.
    - Host-mode versions are ScreenQL, so they may become screens and alert rules like any screen. They still reach
      PAPER only through the existing approval paths.

## Consequences
- **Image size.** The backend image does not change: no Node, TypeScript, R, lupa or sqlglot. Each family has its own
  runner image, pulled only when its profile runs. Approximate sizes, to be measured in AMW-21:
  - `atp-lab-py` (Python, SQL, Lua): 150-200 MB;
  - `atp-lab-node` (JavaScript, TypeScript): 250-300 MB;
  - `atp-lab-r`: 350-450 MB;
  - gVisor's `runsc` on the host: about 50 MB.
  The total is under 1 GB of the 100 GB disk.
  - **Supply chain.** Each image is built from hashed lockfiles: pip `--require-hashes`, `npm ci` with integrity
    hashes, and pinned Debian or r2u packages (or renv with checksums). Nothing is installed at run time.
  - Each image carries an SBOM and passes a vulnerability scan and a secret scan. Each change to an image needs a
    re-review.
- **Memory on the 8 GB KVM.** The current limits (about 6.6 GB) leave out the research worker.
  - Adding the research worker (`MEM_RESEARCH`, 512 MB) brings the sum to about 7.1 GB.
  - Adding the gateway (`MEM_LAB_GATEWAY`, 128 MB) and one Python or Node runner (`MEM_LAB`, 384 MB) brings it to
    about 7.6 GB. That is above the "keep the sum under about 7 GB" rule in `docs/OPERATIONS.md`.
  - So code modes on this host need one of two things: existing limits rebalanced, on measured peaks (G-10), with
    only one family runner up at a time; or a larger host (LAB-6).
  - An R runner (about 896 MB) brings the sum to about 8.1 GB. **R comes last.** It needs a larger host, or a
    rebalance proven by measurement (AMW-Q6).
- **CPU.** No vCPU is reserved for the trading worker.
  - The caps bound Lab and research together at 1.25 vCPU: research worker 0.5, runner 0.5, gateway 0.25. That
    leaves at least 0.75 vCPU for the API, the trading worker and Postgres even under full contention.
  - Low CPU weights (`cpu_shares`) give those services the larger share whenever they compete.
  - G-10 measures the trading worker's cycle time under a burst.
- **Out-of-memory order.** `oom_score_adj` is high for the runners, the gateway and the research worker, and low for
  the trading worker and Postgres. A host-level out-of-memory kill takes Lab work first, and the exits (ADR-0004)
  survive.
- **Code modes arrive late.** None can be switched on before these are done:
  - AMW-21 (runner, gateway and self-test), including the nested-cgroup spike inside runsc;
  - gVisor installed on the host;
  - the G-LAB review.
  The host modes (AMW-20) do not wait for any of this.
- **Operations cost.**
  - The gateway requests the probe suite at runner start, every hour and after each deploy-record change.
  - A failed or stale self-test turns every code mode "unavailable" on the next request (fail closed) and sends one
    operator notification.
  - Approvals must be renewed every 90 days and need two people.
  - The kill path is: turn the family flag off, then stop the profile. Trading, exits and alerts do not depend on the
    Lab.
- **Accepted residual risk.**
  - Bugs in gVisor's own kernel, or in the host kernel calls it uses, remain.
  - Timing side channels on a shared CPU remain.
  - There is no second uid barrier between a jail and its supervisor.
  - A compromised runner can see later users' runs until it is recycled, which bounds that exposure to N runs.
  The threat model lists the rest.

## What ADR-0005 still guarantees
- Strategies that trade are declarative only. Builder strategies, backtests of deployable strategies, PAPER and LIVE
  deployments, and the worker all use `CustomStrategyConfig` through `BaseStrategy.build_signal()`, so exit parity
  holds.
- The AI generator emits the DSL (and, later, ScreenQL or the Pine subset), never code.
- No user-authored code runs in the backend, worker, research worker, gateway, API or frontend process. The only place
  it runs is a jail inside a gVisor runner.
- Host-mode Lab strategies, like DSL strategies, are fully inspectable and explainable. A code-mode result is
  explained only by the rules list its author's code returns, and the page labels it as user-supplied.
- Versions stay immutable: DSL `strategy_versions`, and `lab_versions` with source and compiled SHA-256 hashes.
- ADR-0005's consequence "no sandbox is needed" stays true for everything above. It is narrowed only for the Lab's code
  modes, which run in the runner.

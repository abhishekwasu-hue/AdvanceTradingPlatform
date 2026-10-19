# Strategy Lab - threat model and gate G-LAB

**Slice:** AMW-18 (docs only) · **Status:** proposed, with ADR-0027 · **Date:** 2026-10 (revised after the security
review of PR #264)

**Related documents:**
- [ADR-0027](../adr/0027-strategy-lab-isolated-runner.md): code modes run only in a gVisor runner; amends ADR-0005.
  Its decision 10 extends ADR-0006 to user-authored code.
- ADR-0004 (exits are never blocked), ADR-0006 (the AI never places orders), ADR-0021 (ScreenQL).
- The AMW port plan, `docs/design/AMW_PORT.md` sections 3.1-3.7 and 4.2. This document changes the plan's runner,
  dispatcher and flag-gate design. Where the two differ, this document and ADR-0027 win.

**What this document is for.** It is the threat model for the Strategy Lab, with most of its weight on the six code
modes (Python, SQL, Lua, JavaScript, TypeScript, R). It ends with the **G-LAB checklist** (section 9): the exact
evidence an independent reviewer and the owner must see before any code-mode flag can be turned on.

**The AMW reference code.** AMW's sandbox (`docs/reference/amw-screener-v2.9/src/lab/*`) is untrusted reference
material. It was read, never run. Nothing in this document depends on running it.

---

## 1. System under review

```
 browser --HTTPS--> backend API --rows--> Postgres <--claims lab_runs-- research worker (holds DATABASE_URL,
   (TB-1)            (no user code)          ^                           JWT_SECRET_KEY, SECRETS_ENCRYPTION_KEY;
                                             |                           builds contexts, closed bars only;
                                             |                           writes lab_run_inputs) (TB-2)
                                             | lab_* tables only
                                        lab-gateway (no JWT key, no encryption key; listens on the UDS;
                                             ^       parses frames; validates; charges the budget)
                                             | (TB-3) UDS: the gateway listens, the runner connects, framed JSON
                                             |
               lab-runner-<family> container under gVisor (runsc): no network, no secrets, read-only, one uid
                 runner supervisor --(TB-4)--> per-run jail: user + PID + mount + net + IPC + UTS namespaces,
                                               cgroup (or the fallback), default-deny seccomp, tmpfs work directory
                                                 fresh language worker process --(TB-5)--> user code
 trading worker (bystander: exits, entries, alerts) shares the 2 vCPU / 8 GB host (TB-6: kernel and resources)
```

| # | Component | Trust | Runs user code |
|---|---|---|---|
| C-1 | Frontend (Strategy Lab page, editors, results, logs) | trusted code, untrusted content | no |
| C-2 | Backend API (`/api/lab/*`) | trusted | no |
| C-3 | Postgres (`lab_strategies`, `lab_versions`, `lab_runs`, `lab_run_inputs`, `lab_run_results`, platform controls) | trusted | no |
| C-4 | Research worker (`research-worker`, `app.workers.research_worker`). It holds `DATABASE_URL` (the application role), `JWT_SECRET_KEY` and `SECRETS_ENCRYPTION_KEY` (`docker-compose.yml`). It decrypts the requester's broker token to build contexts from their own session (BT1b). It claims runs and builds contexts, and never touches a runner. | trusted, high value | no |
| C-5 | Lab gateway (`lab-gateway`). It owns the socket and parses every runner frame. It holds no JWT key, no encryption key and no broker token. Its database role is limited to `lab_*` tables. | trusted code that faces untrusted input | no |
| C-6 | Runner supervisor inside `lab-runner-<family>` (jail launcher, cgroup manager, probe suite) | trusted code in an untrusted place: treated as compromisable | no |
| C-7 | Per-run jail with a fresh language worker process | untrusted | yes |
| C-8 | User code (source, parameters) | hostile by assumption | yes |
| C-9 | Host kernel, Docker, gVisor `runsc` | trusted base | no |
| C-10 | Trading worker, alert outbox, exits | trusted, must stay available | no |
| C-11 | CI, image build (Dockerfiles, lockfiles, base images) and `deploy/atp.sh` (writes the deploy record) | trusted process, third-party inputs | no |

**Host modes** (visual and Pine subset) never reach C-5 or the runners. They compile to ScreenQL and are evaluated
by the host's ScreenQL runtime. Their threats are those of ScreenQL (validator, cost cap, Z10-a causal cases,
ADR-0021) plus one more: the Pine translator is a parser of untrusted text. It is a hand-written tokenizer with
input-size and nesting limits, and it never evaluates anything.

## 2. Assets

| # | Asset | Why it matters | Where it lives |
|---|---|---|---|
| A-1 | Trading availability: the trading worker cycle, exits (ADR-0004), alerts | A starved or OOM-killed worker delays exits and stops | C-10, host CPU and memory |
| A-2 | Platform secrets: `DATABASE_URL` (application role), `JWT_SECRET_KEY`, `SECRETS_ENCRYPTION_KEY`, `.env` | Full compromise | The environments of C-2 and **C-4 (research worker)**; host files. Never in C-5, the runners or the jails. |
| A-3 | Tenant secrets: broker tokens, BYOK keys, per-tenant data keys (ADR-0008) | Account takeover, money | Postgres (encrypted); decrypted in C-2 and in C-4 (BT1b contexts) |
| A-4 | Other users' Lab data: source code, parameters, contexts, results, logs | Confidentiality between tenants and users (BT1b) | Postgres; a runner's tmpfs while a run is live |
| A-5 | Market data under licence: the lake, users' own session bars | Redistribution terms (`docs/BROKER_DATA_TERMS.md`) | Postgres / lake, contexts |
| A-6 | Integrity of results, versions, audit rows and operator logs | Trust in research output; non-repudiation | Postgres, log files |
| A-7 | Order path: `execution_core`, Scan Then, auto PAPER, proposals, alerts | PAPER only; user code never trades (ADR-0027 decision 10) | backend, workers |
| A-8 | Gate integrity: approval record, deploy record, self-test record, flag rows | The basis of the G-LAB decision | platform controls |
| A-9 | Shared compute budget: slot seconds, queue slots | Fairness between users, tenants and plans | `lab_runs`, the lease |
| A-10 | The host itself (kernel, Docker daemon, other containers) | Everything above | C-9 |

## 3. Actors

| # | Actor | Capability | Intent assumed |
|---|---|---|---|
| X-1 | Any signed-up user with a trader role, with the family flag on for their tenant | Writes arbitrary source and parameters; starts validate, try, scan and backtest runs; reads their own results and logs | **Hostile.** This is the primary attacker. Sign-up is open, so an account is cheap. |
| X-2 | A user whose account was taken over | As X-1, plus the victim's own strategies | hostile |
| X-3 | A member of the same tenant | As X-1, inside the same organisation | curious or hostile toward teammates (Lab rows are personal) |
| X-4 | AI drafting (Copilot) | Writes DSL, and later ScreenQL or Pine drafts. It never writes code-mode source (port plan C-3) and is not shown code-mode results. | untrusted output |
| X-5 | A compromised runner (after a jail or language escape) | Anything C-6 can do: answer the gateway with crafted frames, forge its self-test, see later runs in the same container until it is recycled | hostile |
| X-6 | Supply chain: base images, npm, CRAN / r2u, PyPI (lupa, sqlglot), the jail tool | Code inside a runner image | possibly hostile |
| X-7 | Operator / SUPER_ADMIN | Turns flags on, writes approvals, has host access | trusted, but every action is audited and needs MFA; the two-person rule applies; can make mistakes |

## 4. Trust boundaries

| # | Boundary | What crosses it | Rule |
|---|---|---|---|
| TB-1 | Browser -> API | source (<= 100,000 characters), parameters, run requests | Existing auth, scopes, tenancy filters (`tenant_id` and `user_id` on every Lab read), rate limits, plan limits, caps on queued runs |
| TB-2 | API -> queue -> research worker -> `lab_run_inputs` | `lab_runs` rows, contexts | At claim time the research worker re-reads the version by id and re-checks owner, flags, the approval (G-12b) and the budget. It never trusts the queued configuration alone, and it never reads a runner frame. |
| TB-3 | Gateway <-> runner (UDS) | Requests from the gateway: source, contexts, limits, seed, clock. Responses from the runner: framed results, logs, status. | **The main boundary.** The gateway listens in a directory it owns. Each runner mounts that directory read-only and connects. The gateway checks `SO_PEERCRED` against the runner's expected uid and refuses a symlinked path. Only the gateway issues requests; an unsolicited frame closes the connection and fails the run (no confused deputy). Every response is untrusted: length-prefixed frames, a hard size cap before parsing, a strict parser, contract validation and read timeouts. |
| TB-4 | Runner supervisor <-> per-run jail | stdin (request), stdout (result frame), stderr (logs) | The jail is the boundary inside the runner: namespaces, cgroup (or the fallback), default-deny seccomp, read-only binds, a fresh process. The supervisor reads output as a capped stream. There is one uid (section 6, gap 2). |
| TB-5 | Language worker <-> user code | the context object, the `atp.*` helpers | Defence in depth only (restricted builtins, `vm` realm, authorizer, curated environments). Never counted as a boundary. |
| TB-6 | Runner container <-> host kernel and resources | syscalls, CPU, memory, pids | **gVisor is required** (ADR-0027 decision 2). Also `cap_drop: ALL`, `no-new-privileges`, `mem_limit`, `cpus`, low `cpu_shares`, `pids_limit` and a high `oom_score_adj`. |
| TB-7 | Results -> UI, logs and downstream | rule names, details, numbers, logs | Rendered as plain text, never as HTML. Report and CSV exports neutralise formula prefixes. Operator logs and notifications escape newlines, ANSI and bidi controls, and carry only closed-set reasons. Results never reach the order path, alerts, proposals or the Copilot (A-7). |

**Entry points for X-1:**
- source text, parameter values and the strategy spec (timeframes, history length);
- run configuration (universe, timeframe, as-of);
- names and descriptions;
- the bytes their code writes to stdout and stderr;
- timing, cancellation, and the number and size of queued runs.

The resource limits are **not** an entry point. They come from the plan and the runner configuration, never from
the author (observation O-3).

## 5. STRIDE

Evidence refers to the G-LAB checklist items in section 9.

| # | STRIDE | Threat | Boundary | ATP control | Evidence |
|---|---|---|---|---|---|
| S-1 | Spoofing | Run, read or publish another user's strategy by id | TB-1 | Every Lab query filters on `tenant_id` and `user_id` (404 otherwise); routes classified in `tenancy_route_table.py`; the research worker re-checks the owner at claim time | G-8 |
| S-2 | Spoofing | Code in a jail, or a compromised runner, uses the socket to submit runs, read other runs' replies, or make the gateway act for it | TB-3, TB-4 | The socket is not mounted into any jail. The jail's own network namespace blocks abstract Unix sockets. The gateway listens, checks `SO_PEERCRED` and refuses symlinks. A runner can only answer the request in flight; an unsolicited frame fails the run. | G-6a, G-6b, G-5b |
| S-3 | Spoofing | A result claims to belong to another run, symbol or time | TB-3 | A nonce per request; results matched to the run id and to the exact symbol list the host sent; the host sets every timestamp and identity | G-7e |
| S-4 | Spoofing | A forged approval, deploy record or self-test turns a code flag on | X-5, X-7 | The approval needs a SUPER_ADMIN with MFA, is audit-logged, expires after 90 days, and is bound to the deploy record that `deploy/atp.sh` writes with `docker inspect`. A runner's report is never trusted. CI probes the same digest. | G-12, G-12b, G-13 |
| S-5 | Spoofing | The flag setter approves their own family | X-7 | Two-person rule: `set_flag` refuses when the approval's writer is the caller | G-12 |
| T-1 | Tampering | A published version is altered after review or a scan | TB-1 | No UPDATE path in code; a PostgreSQL `BEFORE UPDATE` trigger refuses changes to `lab_versions`; SHA-256 of source and compiled form | G-14 |
| T-2 | Tampering | One run changes another run's files, the runtime, or state that a later run reads | TB-4 | Read-only binds of the runtime; a private tmpfs work directory per run, destroyed at the end; a fresh process per run; no pooled workers; no caches outside the tmpfs; container recycle | G-6c, G-8, G-8b |
| T-3 | Tampering | A runner image or a package is replaced upstream | X-6 | Base images pinned by digest; hashed lockfiles; no runtime installs; a fixed module set bound to the approval; SBOM; vulnerability and secret scans | G-11 |
| T-4 | Tampering | Results forged by printing the result marker (AMW's `@@AMW@@` line, observation O-1) | TB-4 | The result travels as one framed message from the worker, separate from the log stream. The contract is checked whatever the source. Forging can only affect the author's own research output. | G-7e |
| T-5 | Tampering | Log injection: user output with newlines, ANSI escapes or bidi controls forges operator log lines or notifications | TB-7 | User text is escaped before any operator log. Notifications carry only closed-set reasons. The runner and gateway use the `x-logging` caps. | G-2, G-7d |
| T-6 | Tampering | Prompt injection: user-authored result strings steer the Copilot or another AI step | TB-7 | Results are not given to the Copilot. If that ever changes: delimited untrusted data, never instructions, never grounding facts, never a tool trigger. | G-16 |
| R-1 | Repudiation | A user denies having run code that misbehaved | TB-1 | An audit row for every validate, try, publish, scan and backtest, with the source SHA-256, the image digest, the seed, the user, the tenant and the time | G-14 |
| R-2 | Repudiation | An operator denies turning a code flag on or approving a family | X-7 | `set_flag` and the approval write are audit-logged with user, MFA state and the bound values | G-12 |
| I-1 | Information disclosure | Secrets read from the environment | TB-3, TB-4 | The runners hold no secrets at all. The gateway holds only its `lab_*` database role. The research worker, which holds the platform keys, never handles runner output. Each jail starts with an empty environment. | G-5b, G-6b |
| I-2 | Information disclosure | Files read outside the jail (`/etc/shadow`, application source, the socket directory, runner configuration) | TB-4, TB-6 | The jail's root holds only read-only binds of its runtime. The runner image holds no application source or secrets. | G-6b |
| I-3 | Information disclosure | Another run's process, `/proc/<pid>/cwd`, environment or memory | TB-4 | Global concurrency 1 (no two runs at once); a PID namespace with a fresh `/proc` per run; seccomp denies `ptrace` and `process_vm_*` | G-6d |
| I-4 | Information disclosure | Another user's data reaches a context | TB-2 | Contexts are built per run from the requester's entitled data only. Context caches are per user when the source is the user's own session. Two users' contexts never share a call. | G-8, G-15 |
| I-5 | Information disclosure | Bulk export of licensed market data through results or logs | TB-7 | The result contract has no series field. Rule details are bounded; logs are capped (16 KB) and owner-only. Contexts carry only what the user may already chart. | G-15 |
| I-6 | Information disclosure | Error text leaks host paths, versions or other users' data | TB-3 | Errors are sanitised (<= 2,000 characters) and built by the gateway from a closed set of reasons, plus the user's own escaped stderr tail | G-7e |
| I-7 | Information disclosure | Timing or cache side channels between runs | TB-6 | gVisor; concurrency 1; coarse timers; no `SharedArrayBuffer` or worker threads; nothing secret is in the runner | G-6f, RR-2 |
| I-8 | Information disclosure | A compromised runner (X-5) reads later users' sources and contexts | TB-3, TB-4 | A fresh jail and process per run; no pooled workers across users; the container is recycled every N runs and after any abnormal end | G-8b, G-8c, RR-9 |
| I-9 | Information disclosure | A cross-user compile or byte-code cache leaks code or data, or serves one user's artefact to another | TB-4 | `NODE_COMPILE_CACHE` off; Python byte code only in the run's tmpfs or not written at all; R lazy-load and byte-compile caches only in the tmpfs; TypeScript output only in the tmpfs | G-8 |
| D-1 | Denial of service | Fork bomb | TB-4 | Per-run `pids.max` (for example 32), or the container's `pids_limit` at concurrency 1; the tree is ended by `cgroup.kill` or by the death of the PID namespace's init | G-7a |
| D-2 | Denial of service | Memory bomb; Node heap growth | TB-4, TB-6 | Per-run `memory.max` and `memory.swap.max = 0`, or the container's `mem_limit` at concurrency 1; Node `--max-old-space-size` | G-7b |
| D-3 | Denial of service | CPU spin, long sleep, escaped child after `setsid` or double fork | TB-4 | Wall-clock and CPU timeouts; `cpu.max` or the container's `cpus`; `cgroup.kill` or PID-namespace teardown ends the whole tree; recycle after an abnormal end | G-7c |
| D-4 | Denial of service | Output flood, or one very long line without a newline, exhausts host memory | TB-4, TB-3 | Streaming read with a hard byte cap; kill on overflow ("output limit exceeded"); nothing buffered beyond the cap; the gateway caps frame size; `x-logging` caps on the containers | G-7d |
| D-5 | Denial of service | Crafted JSON (deep nesting, huge integers, duplicate keys, very long strings) blows up the gateway's parser | TB-3 | Size cap before parse; nesting and string-length limits; integers over 4,300 digits refused; duplicate keys refused; non-finite numbers refused; schema with no extra fields | G-7e |
| D-6 | Denial of service | Inode or tmpfs exhaustion | TB-4 | A tmpfs work directory with size and inode (`nr_inodes`) limits; `RLIMIT_FSIZE`; a read-only root | G-7f |
| D-7 | Denial of service | Queue flooding, or one user or tenant monopolising the single slot | TB-1, TB-2 | Caps on queued runs per user, per tenant and globally; round-robin claiming across users; daily slot-second budgets per user and per tenant; validate and TypeScript compile are charged too | G-9 |
| D-8 | Denial of service | Under-charging: a runner reports less time than it used | TB-3 | The charge is the larger of the gateway's own measured wall time and the runner's report | G-9 |
| D-9 | Denial of service | Lab or research work starves the trading worker or the API of CPU | TB-6 | `cpus` caps (runner 0.5, gateway 0.25, research worker 0.5) and low `cpu_shares` for all three; the research worker alternates studies and Lab builds | G-10 |
| D-10 | Denial of service | A host-level OOM kill takes the trading worker or Postgres | TB-6 | Container memory limits; `oom_score_adj` high for runners, gateway and research worker, low for the trading worker and Postgres | G-2, G-17 |
| D-11 | Denial of service | A runner crash loop or a hung socket stalls the Lab queue | TB-3 | Socket read timeouts; a heartbeat with stale runs marked `interrupted`; a failed self-test fails closed to "unavailable" without retry storms | G-13 |
| E-1 | Elevation of privilege | Container escape through a kernel bug | TB-6 | gVisor required; `cap_drop: ALL`; `no-new-privileges`; no setuid binaries; never `privileged` or `CAP_SYS_ADMIN`. A runc exception needs its own AppArmor profile, cgroup delegation and sysctl posture, all bound into the approval. | G-3, G-4, G-11 |
| E-2 | Elevation of privilege | Jail escape into the runner supervisor | TB-4 | There is one uid, so this boundary rests on namespaces, not on uids. The PID namespace hides the supervisor. The mount namespace leaves out its files and socket. Its code is root-owned on a read-only root. A default-deny seccomp blocks `ptrace`, `process_vm_*`, `mount`, `unshare`, `setns` and the rest of the must-deny set. Concurrency 1 and recycle bound what an escape can reach. | G-4, G-5, G-6d, G-8b |
| E-3 | Elevation of privilege | Language-guard bypass (Python object graph, JavaScript constructor chains, R aliasing, Lua `string.dump`, SQL `ATTACH`) | TB-5 | Guards are defence in depth. The jail still holds after a bypass, and the red-team suite runs every case with the guard **disabled** as well as enabled. | G-6e |
| E-4 | Elevation of privilege | User code reaches the order path | TB-7 | Code-mode results never create entry intents, Scan Then actions, auto-PAPER entries, proposals or alert rules. Code-mode versions cannot be deployed (ADR-0027 decision 10). | G-16 |
| E-5 | Elevation of privilege | A code flag is turned on without review, for a tenant subset, after the image changed, after expiry, or through an unregistered flag name | X-7 | `set_flag` refuses for global and `tenants=[...]` alike; no override. The approval is bound to the deploy record and expires after 90 days. The flag names are registered, default-off constants (`flag_enabled` treats unknown names as on). The run-time re-check also catches hand-edited rows. | G-12, G-12b |
| E-6 | Elevation of privilege | A crafted frame exploits the parser of the process that holds the platform keys | TB-3 | Frames are parsed only by the gateway, which holds no JWT key, no encryption key and no broker token, and whose database role is limited to `lab_*` tables. The research worker, which holds them (C-4), never sees a frame. The gateway's parser is minimal and fuzzed. No frame field is used as a path, SQL or command. | G-5b, G-7e |
| E-7 | Elevation of privilege | A user gets another user to run their code (sharing, marketplace, fork), spending the victim's quota under the victim's identity | TB-1 | Code-mode strategies cannot be shared, listed or forked across users | G-16 |

## 6. The twelve AMW sandbox gaps and the ATP control that closes each

The gaps are from the port plan, section 3.4. They describe `src/lab/sandbox.py` in AMW v2.9.0.

| # | AMW gap | ATP control | Evidence |
|---|---|---|---|
| 1 | **Privilege.** Root, `CAP_SYS_ADMIN` and `apparmor:unconfined` are needed on the app and API containers, so the whole web application runs with a wider kernel surface. | User code never runs in the backend, worker, research worker, gateway or frontend containers. Only the family runners run it, under gVisor (required), as one non-root uid with `cap_drop: ALL` and `no-new-privileges`. Never `privileged`, never `CAP_SYS_ADMIN`. A runc runner is an owner-signed exception only. A CI check reads the compose files. | G-2, G-3, G-4 |
| 2 | **No PID namespace; one shared uid.** `/proc` shows every host process; a run can signal or trace another user's concurrent run and read its `/proc/<pid>/cwd` (R's `strategy.R` and `ctx_data.R`). | Global concurrency 1 removes concurrent neighbours. A PID namespace with a fresh `/proc` per run hides every other process, and seccomp denies `ptrace` and `process_vm_*`. **ATP also uses one uid**, because distinct uids would need `CAP_SETUID`/`CAP_SETGID` or a setuid `newuidmap`. The separation from other runs and from the supervisor is by namespaces, the read-only root and seccomp. That is weaker than a second uid, so it is listed as RR-8. | G-5, G-6d |
| 3 | **No cgroup.** No `pids.max` or `RLIMIT_NPROC`, so a fork bomb works; memory is bounded only by `RLIMIT_AS`, which is not applied to Node. | Per-run cgroup v2 (`memory.max`, `memory.swap.max = 0`, `pids.max`, `cpu.max`) if the AMW-21 spike proves nested cgroups inside runsc. Otherwise, at concurrency 1, the container's `mem_limit`, `pids_limit` and `cpus` are the per-run limits. Either way the limits cover every language, Node included. | G-3, G-7a, G-7b |
| 4 | **Escaped children survive.** A child that calls `setsid` or double-forks leaves the process group, so `killpg` misses it, and it keeps running next to later runs. | `cgroup.kill` (when nested cgroups work) or the death of the PID namespace's init kills every process of the run. The supervisor checks that nothing is left before the next run. The container is recycled after any abnormal end. | G-7c |
| 5 | **No seccomp filter.** The full host-kernel syscall surface stays reachable. | gVisor's user-space kernel stands between the runner and the host kernel. Each jail also has a default-deny seccomp-bpf filter. | G-3, G-4 |
| 6 | **Filesystem.** Only the temporary directories are replaced. The rest of the root filesystem is visible where file modes allow (application source, packages, `/etc`). | The jail's root is built from read-only binds of exactly the runtime it needs, plus a private tmpfs. No host root filesystem; minimal `/etc`; `/dev` limited to `null`, `zero` and `urandom`. The runner image holds no application source or secrets. | G-6b |
| 7 | **Unbounded output buffering.** `communicate()` reads everything into host memory before truncating. | The supervisor reads stdout and stderr as a stream with a hard byte cap and kills the run on overflow. The gateway caps frame size before parsing. | G-7d, G-7e |
| 8 | **Quotas per process, in memory.** The dashboard and the API each keep their own counters. | One database lease for global concurrency; queued-run caps, slot-second budgets per user and per tenant, and round-robin claiming, all in the database; the charge measured by the gateway | G-9 |
| 9 | **Self-test cached once per process** (`lru_cache`), never repeated after an environment or image change. | The gateway requests the probe suite at runner start, every hour and after each deploy-record change. A result is accepted only for the digest in the trusted deploy record. A stale or failed result fails closed. CI runs the same suite against the same digest. | G-13 |
| 10 | **Unsandboxed mode** on Windows or with `AMW_ALLOW_UNSANDBOXED=1`. | None exists. No code path runs user code outside a runner on any platform. Without gVisor or a runner, code modes are "unavailable". A test asserts there is no such switch. | G-2, G-13 |
| 11 | **Bypassable deny-list language guards** (for example R's `\bsystem2?\s*\(` misses `f <- system; f("...")`). | Guards are defence in depth only. R gets a curated environment (parent is a function allow-list, not `baseenv()`) and JSON contexts. The red-team suite also runs with guards disabled, to prove the jail alone holds. | G-6e |
| 12 | **World-writable (1777) work directory; `RLIMIT_FSIZE=0` does not stop directories or inodes.** | A private tmpfs per run (mode 0700), with size and `nr_inodes` limits; a read-only root elsewhere | G-6c, G-7f |

### Further observations from the reference code

These were found while reading the reference code for this slice. They are not in the plan's list of twelve.

| # | Observation | ATP control |
|---|---|---|
| O-1 | `engine._parse_worker` takes the last stdout line that starts with `@@AMW@@` as the result. Where user code can write to the same stdout, it can forge that line. | One framed result message per call, on a stream separate from logs; contract validation; results bound to the run nonce and the symbols the host sent (S-3, T-4) |
| O-2 | R contexts are serialised as R **source** (`_r_lit`), and dict keys are put inside backticks without escaping. A key holding a backtick would break into R code. Keys are host-built today, so the risk is low, but data must never become code. | Contexts go to every language as JSON data, parsed inside the runtime |
| O-3 | Resource limits are part of the author's strategy spec (`ResourceLimits`: timeout up to 120 s, memory up to 2,048 MB, output up to 4 MB per context). The author chooses their own limits within high ceilings. | Limits come from the plan and the runner configuration. The spec cannot raise them. |
| O-4 | The TypeScript compile step runs with no memory limit (`mem_mb=None`) and at least a 20 s timeout. | The compile step is a jailed run like any other, with the same limits, and it is charged to the budget |

## 7. Resource model, limits and budgets (provisional; measured in AMW-21 and G-10)

**Runner model.**
- There is one runner container per enabled family (`lab-runner-py`, `lab-runner-node`, `lab-runner-r`), each under
  its own compose profile, started only when its family flag is to be on.
- One `lab-gateway` runs while any family is on.
- Global concurrency is 1 across all runners, held by one database lease that the gateway takes per call.
- On the 8 GB host, only one family runner is up at a time (LAB-6).

**Memory (sum of container limits, `docs/OPERATIONS.md` 1.2b; the rule is "under about 7 GB").**

| Step | Adds | Sum |
|---|---|---|
| Today's containers | - | ≈ 6.6 GB |
| + `research-worker` (`MEM_RESEARCH`, new) | 512 MB | ≈ 7.1 GB |
| + `lab-gateway` (`MEM_LAB_GATEWAY`) | 128 MB | ≈ 7.2 GB |
| + one Python or Node runner (`MEM_LAB`) | 384 MB | ≈ 7.6 GB |
| or + the R runner instead (`MEM_LAB_R`) | 896 MB | ≈ 8.1 GB |

The Python or Node step is already over the rule. Code modes on this host therefore need one of:
- existing limits rebalanced on measured peaks, recorded in `docs/BENCHMARKS.md` (G-10), so the sum stays under about
  7 GB with one family runner up;
- a larger host.

R needs a larger host unless measurement proves otherwise (AMW-Q6, LAB-6).

**CPU and out-of-memory order.**

| Container | `cpus` | `cpu_shares` (cgroup v2 `cpu.weight`) | `oom_score_adj` |
|---|---|---|---|
| `lab-runner-<family>` | 0.5 | 128 | 1000 |
| `lab-gateway` | 0.25 | 256 | 800 |
| `research-worker` | 0.5 | 256 | 500 |
| trading `worker` | unchanged | 1024 (default) | -500 |
| `postgres` | unchanged | 1024 (default) | -800 |

No vCPU is reserved for the trading worker. Under full contention the Lab and research containers together cannot
exceed 1.25 vCPU, which leaves at least 0.75 vCPU for the API, the trading worker and Postgres, and the weights give
those the larger share whenever they compete. G-10 measures the trading worker's cycle time under a burst.

**Per call.**

| Limit | Value | Where enforced |
|---|---|---|
| Wall-clock | 15 s (R: 30 s) | supervisor timer; the gateway's own deadline |
| CPU time | 10 s (R: 20 s) | `cpu.max` (or `cpus`) + `RLIMIT_CPU` |
| Memory | 256 MB (R: 768 MB), swap 0 | `memory.max` (or the container's `mem_limit`) |
| Processes | 32 | `pids.max` (or `pids_limit`) |
| Open files | 64 | `RLIMIT_NOFILE` |
| Work directory | 4 MB tmpfs, 256 inodes, mode 0700 | tmpfs `size`, `nr_inodes` |
| Output | result frame 256 KB; logs 16 KB stored; stderr read cap 64 KB | supervisor stream cap, gateway frame cap |
| Source | 100,000 characters | API and runner |
| Contexts per call | 50 (R: 10) | research worker, gateway |
| Try run | at most 5 symbols, drafts only | API |
| Container recycle | after 50 runs, and after any abnormal end | gateway refuses further calls from that sandbox (keyed on the host-kernel `SO_PEERCRED` pid of the runsc sandbox); the host-side `deploy/atp.sh` agent and `restart: unless-stopped` restart it. The gateway has no Docker access. |

**Queue fairness.**
- Queued runs are capped at 3 per user, 10 per tenant and 100 globally.
- Claiming is round-robin: the next run comes from the user served least recently.
- The research worker alternates job kinds: after each AI research study it takes up to five Lab context builds, so
  neither kind starves the other.

**Budget per plan, in slot seconds** (`lab_cpu_seconds_per_day`, AMW-1, counted as slot seconds; LAB-1).
- A slot second is one second during which a run holds the single global slot.
- Each call is charged the larger of the gateway's measured wall time and the runner's reported CPU time, with a
  1-second minimum. Validate and TypeScript compile are charged too.

| Plan | Per user per IST day | Per tenant per IST day | Reason |
|---|---|---|---|
| Free | 0 (host modes only) | 0 | Sign-up is open, so a free account is the cheapest attacker. Host modes run no user code. |
| Pro | 900 | 3,600 | About 60 full calls a day per user; a team cannot fill the slot |
| Business | 3,600 | 14,400 | Four times Pro |

The single slot caps the host-wide total at 86,400 slot seconds a day, whatever the plans add up to.

## 8. Residual risks

| # | Risk | Why it remains | Mitigation / acceptance |
|---|---|---|---|
| RR-1 | A bug in gVisor's sentry, or in the host kernel calls it makes | No isolation is perfect | gVisor required; runners hold nothing of value; prompt runsc and host patching; the approval binds the runsc version. Accepted by the owner at G-LAB. |
| RR-2 | Timing and cache side channels (CPU caches, Spectre-class) between runs, or between a run and host processes | A shared 2 vCPU host | Concurrency 1; coarse timers; no `SharedArrayBuffer`; no secrets in the runners; host mitigations on. Accepted. |
| RR-3 | A compromised runner forges its self-test | The probe runs inside the thing it tests | The gate never trusts the runner. It trusts the deploy record (`docker inspect`) and CI's probe run on the same digest. The self-test is a liveness signal only. Accepted. |
| RR-4 | A runc exception, if the owner signs one, widens the runner's kernel surface (AppArmor profile, delegated cgroups, the userns sysctl) | gVisor may not install on Hostinger KVM 2 (AMW-Q3) | Off by default. It needs the owner's signature; its profile, delegation and sysctls are in G-2 and G-4 and bound into the approval. The alternative is a separate host with no secrets. |
| RR-5 | Language guards are bypassable | Deny-lists and realms are not boundaries | Recorded as defence in depth; the suite runs with guards off |
| RR-6 | An operator with database or host access bypasses `set_flag` by editing the control row | Operators are trusted (X-7) | The run-time re-check (G-12b) refuses without a valid approval bound to the deploy record. Audit logs. Two-person rule. |
| RR-7 | Results are user-authored and can be misleading (look-ahead is impossible, but bad logic is not) | Research output by design | Labelled as user-supplied research; never trades; the disclaimer is shown |
| RR-8 | One uid: a namespace escape lands on the supervisor's uid with no second uid barrier | Distinct uids need `CAP_SETUID`/`CAP_SETGID` or a setuid helper, which the runner does not keep | gVisor below; the supervisor's code is root-owned and read-only; concurrency 1; recycle. Accepted. |
| RR-9 | A compromised runner sees later users' runs until it is recycled | Recycling every run would cost a container start per call | Recycle every 50 runs and after any abnormal end, enforced by the gateway refusing service to that sandbox and by a host-side restart, so a runner that will not exit cannot extend its window (G-8c); a fresh process per run; no pooled workers. Accepted at N = 50. |
| RR-10 | The 8 GB host has no headroom for code modes without a rebalance | Host size | Section 7; LAB-6; R needs a larger host |
| RR-11 | Abuse through many free or throwaway accounts | Open sign-up | Free plan has no code-mode budget; per-tenant budgets and queue caps; the flag can be scoped to tenants |

## 9. Gate G-LAB - the security review checklist

No code-family flag (`strategy_lab_python`, `strategy_lab_sql_lua`, `strategy_lab_js`, `strategy_lab_r`) can be
turned on, globally or for any tenant, until every item below has its evidence attached to the review record.
`set_flag` and the run-time check enforce the result, not the list: they need the approval record, and the approval
may only be written once this list is complete.

For each item, **Evidence** is the artefact the reviewer reads and **Pass** is the condition that must hold. "CI"
means the test runs in CI against the real runner image digests. "Host" means it was also run once on the production
host profile (2 vCPU / 8 GB) and the output was attached.

**Records and design**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-1 | ADR-0027 and this threat model | ADR status, review notes | ADR-0027 **accepted** by the owner. Every STRIDE row has a control and an evidence item, or a residual-risk entry the owner has accepted. |

**Containers, isolation and the gateway**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-2 | Container configuration | `docker-compose*.yml`, plus a CI test that parses them | **Runners:** `runtime: runsc`, `network_mode: none`, `read_only: true`, one non-root `user`, `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit`, `mem_limit`, `cpus`, `cpu_shares`, `oom_score_adj`, `logging: *default-logging`; no `env_file`, and no environment entry except the socket path; the socket directory mounted read-only; no database or secrets volume. **Gateway:** no `JWT_SECRET_KEY` or `SECRETS_ENCRYPTION_KEY`; no Docker socket mount and no Docker API access; internal network only; limits and logging as above. **Research worker:** gains `MEM_RESEARCH`, `cpus`, `cpu_shares` and `oom_score_adj`. **Trading worker and Postgres:** low `oom_score_adj`. **Backend, worker, frontend:** no new capability, `privileged`, `security_opt` or device. No code path or switch runs user code outside a runner. **runc exception only:** the custom AppArmor profile, the cgroup delegation and the sysctl posture (`kernel.apparmor_restrict_unprivileged_userns`, `user.max_user_namespaces`), with the owner's signed exception. |
| G-3 | gVisor (required) | `runsc --version`; the Docker `daemon.json` runtime entry; the `runsc` flags (`--network=none`, and the host-UDS setting the connect-only socket needs); the probe suite output under `runsc` on the host; **the AMW-21 nested-cgroup spike report** | The runners run under runsc and the probe suite passes on the host. The spike states whether `memory.max`, `pids.max`, `cpu.max` and `cgroup.kill` work inside runsc. If they do not, G-7a to G-7c pass using the fallbacks (PID-namespace teardown; container limits at concurrency 1). |
| G-4 | seccomp | The per-jail filter (a default-deny allow-list) and its hash; for a runc exception, the container profile and its diff against Docker's default, with every added call justified | The filter **denies by default**. A test calls each call in the tested **must-deny set** from inside the jail and expects `EPERM` or `ENOSYS`: `ptrace`, `process_vm_readv`, `process_vm_writev`, `mount`, `umount2`, `unshare`, `setns`, `pivot_root`, `keyctl`, `add_key`, `bpf`, `perf_event_open`, `userfaultfd`, `kexec_load`, `init_module`, `finit_module`, `clock_settime`, `settimeofday`, `io_uring_setup`, `socket` (every family except `AF_UNIX` socketpairs if a worker needs them). |
| G-5 | Per-run jail configuration | The jail tool version (pinned), its configuration file and hash, and one dumped jail's `/proc/self/status`, `uid_map`, mount table and cgroup files (or the fallback) | New user, PID, mount, network, IPC and UTS namespaces. `CapEff` is 0. `uid_map` maps the single runner uid only. The mount table shows only read-only runtime binds and the tmpfs work directory. The supervisor's files and socket are absent. The limits match section 7. The supervisor's code is root-owned and read-only. |
| G-5b | Gateway privileges and the socket | The gateway's environment dump; its database role grants; socket and directory permissions; tests | No JWT key, no encryption key, no broker token; no Docker socket and no Docker API reachable; any query on a non-`lab_*` table is denied. The gateway listens. A runner connects through a read-only mount. A wrong `SO_PEERCRED` uid is refused, and so is a symlinked socket path or directory. An unsolicited frame from a runner closes the connection and fails the run. The research worker has no code path that reads a runner frame. |

**Escape tests** (the red-team suite; CI and host; each case is one test with its expected outcome)

| # | Item | Cases | Pass |
|---|---|---|---|
| G-6a | Network egress and side channels to the supervisor | TCP connect to an outside IP and to the Docker host gateway; UDP send; DNS lookup; ICMP; IPv6; abstract Unix socket connect; netlink socket; connect to the gateway's socket path; `/dev/shm` and `memfd_create` handed or shared toward the supervisor | Every attempt fails; no packet leaves (the runner has no interface but loopback). `/dev/shm` and memfd are private to the run and are not a channel to the supervisor. |
| G-6b | File reads outside the jail | `/etc/shadow`, the runner's `/etc/passwd`, `/proc/1/environ`, `/proc/1/cmdline`, `/proc/self/mountinfo` looking for host paths, the runner's configuration, the socket directory, any application source path, `/sys/fs/cgroup` above the run's own, the Docker socket | Not present or `EACCES`. The environment canary set in the supervisor is never visible. |
| G-6c | Writes outside the work directory | Write into the runtime binds, `/tmp` outside the jail, `/dev/shm` outside the run, `/proc/sys`, the cgroup files | All fail; the work directory is the only writable path |
| G-6d | `/proc` and other runs | In CI, two runs at once (concurrency raised to 2 for the test, with different users and tenants): list `/proc`; read the other's `/proc/<pid>/cwd`, `environ` and `maps`; `kill` and `ptrace` the other and the supervisor; `process_vm_readv` | Each run sees only its own PIDs. Every cross-run and run-to-supervisor call fails, even though all of them share one uid. |
| G-6e | Per language, with the guard enabled **and** disabled | Python: `__subclasses__`, frame and `gc` walking, `__builtins__` recovery, `importlib`, `ctypes`. JavaScript / TypeScript: `constructor.constructor`, `Proxy`, `import()`, `WebAssembly`, `process.binding`, `eval` and `Function` from strings. R: aliasing (`f <- system`), backtick calls, `.Internal`, `.Call`, `do.call`, environment walking, `get` / `match.fun`. Lua: `string.dump`, `load`, metatables, `debug`, `collectgarbage`. SQL: `ATTACH`, `load_extension`, `PRAGMA`, recursive-CTE blow-ups, `randomblob` size | Guard on: refused with a clear error. Guard off: whatever the code reaches, G-6a to G-6d still hold. |
| G-6f | Timers, randomness and the clock | The same version, inputs and seed run twice; clock reads in each language; `performance.now()` resolution; `SharedArrayBuffer`, `Atomics` and `worker_threads`; `PYTHONHASHSEED` | Identical outputs on both runs. The clock returns the host-set value (or is unavailable). Timers are coarse (1 ms or worse). `SharedArrayBuffer` and worker threads are absent. The hash seed is fixed. The run row records the seed and the image digest. |
| G-7a | Fork bomb | `while True: os.fork()` in Python, and the equivalents in Node, R and Lua where they exist | Contained by `pids.max` or `pids_limit`; host PID count unchanged; the next run starts normally |
| G-7b | Memory bomb | Grow an allocation without limit (Python list, Node array and Buffer, R vector, Lua table) | The run is OOM-killed inside its own cgroup or container and reported "memory limit exceeded". No other container is touched (`docker stats` and the kernel log are attached). |
| G-7c | Timeout kills the whole process tree | CPU spin; `sleep` past the limit; a child after `setsid`; a double-forked daemon; a child that ignores `SIGTERM`; cancel mid-run | Every process ends within 1 s of the deadline or the cancel (`cgroup.kill`, or PID-namespace teardown). The supervisor confirms nothing of the run survives before the next run. The container is recycled. |
| G-7d | Output flood and log injection | Gigabytes to stdout until the timeout; the same on stderr; one line with no newline; millions of tiny writes; output after the result frame; stderr with newlines, ANSI escapes and bidi controls (U+202A-U+202E, U+2066-U+2069) | Killed at the cap with "output limit exceeded". The gateway's RSS stays within a fixed bound (measured). The operator logs and notifications show the user text escaped on one line, or not at all. The container logs respect the `x-logging` caps. |
| G-7e | Crafted JSON | Oversized frame; nesting depth 10,000; duplicate keys; `NaN`, `Infinity`, `1e400`; an integer of more than 4,300 digits; wrong types; extra fields; lone UTF-16 surrogates and control characters; strings over the limit; results for a symbol not sent; a missing or wrong nonce; timestamps in the output; a stop on the wrong side; a forged `@@AMW@@`-style line in the logs | Each is refused by the gateway with a sanitised error and no partial store. A fuzz run of the frame parser (at least 1 million cases) shows no crash and no unbounded memory. |
| G-7f | Inode and disk exhaustion | Create files and directories until failure; large `ftruncate`; sparse files | Stops at the tmpfs `size` and `nr_inodes` limits; the host disk is untouched |

**Isolation between tenants, users and runs**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-8 | Per-tenant and per-user isolation | Tests with two tenants and two users each | User B gets 404 on user A's strategies, versions, runs, results and logs, whether A is in the same tenant or another one. All Lab routes are classified in `tenancy_route_table.py`. A run's contexts hold only the requester's entitled data. A context cache built from a user's own session is never read for another user. Two users' contexts never share a call. Residue: run A writes to its work directory and run B, afterwards, finds nothing. No compile or byte-code cache is written outside the run's tmpfs: `NODE_COMPILE_CACHE` unset, no `__pycache__` or `PYTHONPYCACHEPREFIX` writes, no R lazy-load or byte-compile cache, no TypeScript output kept. One user's quota cannot be spent by a teammate. |
| G-8b | No state survives between runs | Tests per language; the recycle counter | Run A changes interpreter state and run B checks it: Python module globals and `sys.modules`; Node globals, `require` cache and prototype pollution (`Object.prototype`, `Array.prototype`); the R global environment and options; Lua globals; SQLite temporary tables. Nothing is visible to run B. Each run is a new process (a different PID namespace and start time). The container is recycled after 50 runs and after any abnormal end. |
| G-8c | Recycle cannot be refused by a runner | Tests with a runner supervisor patched to ignore the recycle request and stay connected | After 50 runs, and after an abnormal end, the gateway serves no further call from that sandbox (keyed on its host-kernel `SO_PEERCRED` pid, not on anything the runner reports); code modes for that family are "unavailable" until a new sandbox connects. The host-side agent stops and restarts the container. |

**Resources and the trading host**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-9 | Quotas and fairness | Tests | Refused at each limit: per-user and global concurrency (one database lease shared by all runners), queued-run caps (per user, per tenant, global), per-user and per-tenant slot-second budgets, source size, contexts per call, symbols per scan, plan limits. Claiming is round-robin across users. Validate and TypeScript compile are charged. The charge is the larger of the gateway's measured wall time and the runner's report: a runner that under-reports is still charged in full. The research worker alternates studies and Lab builds. |
| G-10 | Budget per plan, memory budget and load test | `lab_cpu_seconds_per_day` values (as slot seconds) in `backend/app/plans/registry.py`, with LAB-1 answered; the rebalanced `MEM_*` table, with LAB-6 answered; a load test on the 8 GB profile; `docs/BENCHMARKS.md` entries | The budgets are enforced (a test spends them and is refused). The sum of container limits, including the research worker, gateway and one runner, stays under about 7 GB, backed by measured peaks. Under a burst at the configured concurrency, the API's p95 latency rises by at most 10 % and the trading worker's cycle time by at most 5 % (LAB-4). Peak RSS per family and image sizes are recorded. |

**Supply chain**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-11 | Images, lockfiles, SBOM and scans | Dockerfiles; lockfiles; SBOM (SPDX or CycloneDX) per runner image; the vulnerability scan; an image secret scan; the module list per family | Base images pinned by digest. Python packages installed with `pip --require-hashes`; Node with `npm ci` and lockfile integrity; Debian and r2u packages pinned to exact versions (or renv with checksums). The jail tool and runtimes are pinned to releases at least two weeks old. Nothing is installed at run time; there is no package manager or compiler in the final stage. The fixed module set per family is enumerated (for example Python: `math`, `statistics`; SQL: `sqlite3` + `sqlglot`; Lua: `lupa`; Node: core + `typescript`; R: `base`, `stats`, `utils`) and its hash is bound into the approval. No setuid or setgid binary. No high or critical finding without a recorded exception. The secret scan is clean. |

**Gate mechanics, self-test and result use**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-12 | The flag switch | Tests on `set_flag` and the flag registry | Turning a code-family flag on is refused, **both globally and with `tenants=[...]`**, when: `strategy_lab` is off; the self-test is stale or failed; the approval is missing, expired (older than 90 days), or does not match the deploy record (image digest, runtime kind and runsc version, jail configuration hash, worker hash, module set hash, compose service block hash, and the AppArmor profile hash for a runc exception); or the caller wrote the approval (two-person rule). There is no override argument. Each refusal is audit-logged. Turning the flag off cancels the family's queued runs. **Registry test:** every Lab flag name used in code is a registered constant, present in `FEATURE_FLAGS` and in `DEFAULT_OFF_FLAGS` (because `flag_enabled` returns on for unknown names). |
| G-12b | Run-time enforcement | Tests on the research worker's claim and the gateway's pre-call check | Before every run, both refuse (fail closed, with a closed-set reason) when: the approval is missing, mismatched or expired; the self-test is stale; the deploy record is missing; or `strategy_lab` is off. One test turns a family flag on through a hand-edited control row (bypassing `set_flag`) and shows that no run executes. |
| G-13 | Self-test, who verifies it, and fail closed | Tests; the stored self-test record; the deploy record; the CI probe record for the same digest; the operator notification | The gateway requests the probe suite at runner start, hourly and after each deploy-record change. It verifies the result automatically and accepts it only when it ran in the image digest of the trusted deploy record (`deploy/atp.sh`, `docker inspect`). The reviewer verifies that CI's probe run passed for that same digest before writing the approval. The runner's own report is never the trust anchor. A stale or failed result turns every code mode "unavailable" on the next request, with one operator notification. A missing runner or missing gVisor means "unavailable", never a fallback. |
| G-14 | Audit, immutability and reproduction | Tests | Every validate, try, publish, scan and backtest writes an audit row with the source SHA-256, the image digest and the seed. An UPDATE on `lab_versions` is refused by the trigger. A run can be replayed from its recorded version, inputs, seed and digest with the same output. |
| G-15 | Data minimisation | Tests | Contexts carry only completed bars, cut at the decision bar (Z10-a), from the requester's entitled sources. No key, token or account field can reach a context: a canary secret planted in every store never appears in any context or in the gateway's memory. Logs are capped, owner-only, sanitised, and rendered as plain text. |
| G-16 | Results never trade, never spread, never instruct | A test over every producer and consumer | Code-mode results never reach `execution_core` intents, Scan Then, auto PAPER, proposals, alert rules or the Copilot. A code-mode version cannot be deployed. A code-mode strategy cannot be shared, listed in the marketplace or forked across users. |

**Operations and sign-off**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-17 | Kill path and OOM order | A runbook entry and its rehearsal log on the host; an OOM drill | Family flag off, then the family's profile and the gateway stopped. Trading, exits and alerts are unaffected during the rehearsal (worker cycle log attached). In a host memory-pressure drill, the kernel picks the runner first and the trading worker and Postgres survive. |
| G-18 | User-facing text | The page copy and terms | The page says what isolation means, the limits and budgets, that results are research only and never trade, that code strategies cannot be shared, and the terms for uploaded code. English only, and it passes the wording check. |
| G-19 | Sign-off and approval record | The review record | An independent reviewer (not the author) and the owner sign off. The `lab_security_review` record is written per family by a SUPER_ADMIN with MFA, bound to the values in G-12, with a 90-day expiry. A different principal turns the flag on. |

**Per family.**
- Python is reviewed first (AMW-22).
- Each later family (SQL and Lua, JavaScript and TypeScript, R) repeats these before its own flag:
  - G-2 to G-5 (its runner and jail);
  - G-6e (its language cases);
  - G-6f, G-7, G-8b and G-8c (with its runtime);
  - G-10 (its memory and CPU);
  - G-11 (its image);
  - G-19.

**Re-review and renewal.**
- Any change to a runner image, the runsc version, the jail configuration, a worker script, the module set or the
  runner's compose service block voids that family's approval automatically, because the deploy record no longer
  matches. G-2 to G-8c, G-11 and G-19 are then repeated for that family.
- An approval also lapses after 90 days. Renewal repeats G-3, G-6, G-7, G-11 (a fresh scan) and G-19.

## 10. Review follow-ups to settle before acceptance (G-1)

The second security review of this design (PR #264) passed it as a proposed ADR and listed these items. Each is
settled in AMW-19 or AMW-21, before G-1, and the matching G-LAB row is tightened then.

1. **fd hygiene in the jail.** The jail inherits only stdin, stdout and stderr (`close_range` / `O_CLOEXEC`), and
   never the supervisor's connected gateway socket. G-5 dumps `/proc/self/fd` from inside the jail. The supervisor
   sets `PR_SET_DUMPABLE=0` and keeps no writable state reachable from the jail's mount namespace.
2. **Namespaces under runsc as non-root.** The AMW-21 spike proves the jail tool can create user, PID, mount,
   network, IPC and UTS namespaces under runsc with `cap_drop: ALL`, not only nested cgroups. If it cannot, code
   modes do not ship, or the design goes back for review. "runsc plus a bare process" is never a silent fallback.
3. **The gateway's database role.** The gate checks in G-12b read the flag row, `lab_security_review`, the deploy
   record and plan or tenant data, none of which are `lab_*` tables. They are exposed to the gateway only through
   read-only `lab_gate_*` views (or SECURITY DEFINER functions) that return just those keys. The design names where
   the self-test record lives and who may write it.
4. **Least privilege per table.** The gateway gets SELECT on `lab_runs` and `lab_run_inputs` (and `lab_versions` only
   if the source is not copied into `lab_run_inputs`), INSERT on `lab_run_results`, and UPDATE on the status, charge,
   log and error columns of `lab_runs`. No access to `lab_strategies`: the research worker copies a draft's source
   into `lab_run_inputs` for "try" runs.
5. **`lab_run_inputs` cannot cross users.** A composite foreign key from (`run_id`, `tenant_id`, `user_id`) to
   `lab_runs`. Rows are per run, never deduplicated or reused across runs or users (lake contexts included). The
   gateway selects inputs only for the run whose lease it holds and asserts the owner matches. Rows are deleted on
   completion or after a short time-to-live, and are covered by `erase_user` and the tenant cascade, because they
   hold users' own broker-session bars (BT1b).
6. **G-13 wording.** The self-test is a liveness signal only. The trust anchors are the deploy record and CI's probe
   run for the same digest; the gateway does not claim to verify the digest the runner ran in.
7. **`SO_PEERCRED` under runsc.** The peer the gateway sees is the sandbox's host process, not the in-container uid.
   G-5b defines the expected peer and tests it on the host. G-8c uses the same value.
8. **G-4 and G-2 wording.** Deny `socket()` entirely in the jail; allow `socketpair(AF_UNIX)` only where a worker
   needs it. The socket directory is mounted into the gateway and the runners only, never into the backend or the
   research worker.
9. **Two-person rule with one SUPER_ADMIN.** If the owner is the only SUPER_ADMIN, no code family can be turned on.
   Recorded under LAB-3.

# Strategy Lab - threat model and gate G-LAB

**Slice:** AMW-18 (docs only) · **Status:** proposed, with ADR-0027 · **Date:** 2026-10

**Related documents:**
- [ADR-0027](../adr/0027-strategy-lab-isolated-runner.md) (code modes run only in an isolated runner; amends
  ADR-0005);
- ADR-0004 (exits are never blocked), ADR-0006 (the AI and user code never place orders), ADR-0021 (ScreenQL);
- the AMW port plan, `docs/design/AMW_PORT.md` sections 3.1-3.7 and 4.2.

**What this document is for.** It is the threat model for the Strategy Lab, with most of its weight on the six code
modes (Python, SQL, Lua, JavaScript, TypeScript, R). It ends with the **G-LAB checklist** (section 9): the exact
evidence an independent reviewer and the owner must see before any code-mode flag can be turned on.

**The AMW reference code.** AMW's sandbox (`docs/reference/amw-screener-v2.9/src/lab/*`) is untrusted reference
material. It was read, never run. Nothing in this document depends on running it.

---

## 1. System under review

```
 browser --HTTPS--> backend API --rows--> Postgres <--claims lab_runs-- research worker (dispatcher)
   (TB-1)            (no user code)                                        | builds contexts (closed bars only)
                                                                           | (TB-3) UDS, framed JSON
                                                                           v
                                     lab-runner container: no network, no secrets, read-only, gVisor / seccomp
                                       runner supervisor (own uid) --(TB-4)--> per-run jail: user + PID + mount +
                                                                               net + IPC + UTS namespaces, slot uid,
                                                                               cgroup v2, seccomp, tmpfs work dir
                                                                                 language worker --(TB-5)--> user code
 trading worker (bystander: exits, entries, alerts) shares the 2 vCPU / 8 GB host (TB-6: kernel and resources)
```

| # | Component | Trust | Runs user code |
|---|---|---|---|
| C-1 | Frontend (Strategy Lab page, editors, results, logs) | trusted code, untrusted content | no |
| C-2 | Backend API (`/api/lab/*`) | trusted | no |
| C-3 | Postgres (`lab_strategies`, `lab_versions`, `lab_runs`, `lab_run_results`, platform controls) | trusted | no |
| C-4 | Research worker and dispatcher (`backend/app/lab/dispatch.py`). It holds database credentials, so it is the most valuable target past the runner. | trusted | no |
| C-5 | `lab-runner` supervisor (UDS server, jail launcher, cgroup manager, probe suite) | trusted code in an untrusted place: treated as compromisable | no |
| C-6 | Per-run jail with a language worker | untrusted | yes |
| C-7 | User code (source, parameters) | hostile by assumption | yes |
| C-8 | Host kernel, Docker, gVisor `runsc` | trusted base | no |
| C-9 | Trading worker, alert outbox, exits | trusted, must stay available | no |
| C-10 | CI and image build (Dockerfiles, base images, packages) | trusted process, third-party inputs | no |

**Host modes** (visual and Pine subset) never cross TB-3. They compile to ScreenQL and are evaluated by C-4's ScreenQL
runtime. Their threats are those of ScreenQL (validator, cost cap, Z10-a causal cases, ADR-0021) plus one more: the
Pine translator is a parser of untrusted text. It is a hand-written tokenizer with input-size and nesting limits, and
it never evaluates anything.

## 2. Assets

| # | Asset | Why it matters | Where it lives |
|---|---|---|---|
| A-1 | Trading availability: the trading worker cycle, exits (ADR-0004), alerts | A starved worker delays exits and stops | C-9, host CPU and memory |
| A-2 | Platform secrets: database credentials, `.env`, JWT keys, envelope master key | Full compromise | C-2, C-4 environments, host files |
| A-3 | Tenant secrets: broker tokens, BYOK keys, per-tenant data keys (ADR-0008) | Account takeover, money | Postgres (encrypted), backend memory |
| A-4 | Other users' Lab data: source code, parameters, contexts, results, logs | Confidentiality between tenants and users (BT1b) | Postgres, runner work directories while running |
| A-5 | Market data under licence: the lake, users' own session bars | Redistribution terms (`docs/BROKER_DATA_TERMS.md`) | Postgres / lake, contexts |
| A-6 | Integrity of results, versions and audit rows | Trust in research output; non-repudiation | Postgres |
| A-7 | Order path: `execution_core`, Scan Then, auto PAPER, proposals | PAPER only; user code must never trade (ADR-0006) | backend, workers |
| A-8 | Runner integrity: image, seccomp profile, worker scripts, self-test record, approval record | The basis of the G-LAB decision | images, platform controls |
| A-9 | Shared compute budget: runner CPU seconds, queue slots | Fairness between users and plans | runner, `lab_runs` |
| A-10 | The host itself (kernel, Docker daemon, other containers) | Everything above | C-8 |

## 3. Actors

| # | Actor | Capability | Intent assumed |
|---|---|---|---|
| X-1 | Any signed-up user with a trader role, with the family flag on for their tenant | Writes arbitrary source and parameters; starts validate, try, scan and backtest runs; reads their own results and logs | **Hostile.** This is the primary attacker. Sign-up is open, so an account is cheap. |
| X-2 | A user whose account was taken over | As X-1, plus the victim's own strategies | hostile |
| X-3 | A member of the same tenant | As X-1, inside the same organisation | curious or hostile toward teammates (Lab rows are personal) |
| X-4 | AI drafting (Copilot) | Writes DSL, and later ScreenQL or Pine drafts. It never writes code-mode source (C-3). | untrusted output |
| X-5 | A compromised runner (after a jail or language escape) | Anything C-5 can do: answer the dispatcher with crafted frames, forge its self-test | hostile |
| X-6 | Supply chain: base images, npm, CRAN, PyPI (lupa, sqlglot), the jail tool | Code inside the runner image | possibly hostile |
| X-7 | Operator / SUPER_ADMIN | Turns flags on, writes the approval record, has host access | trusted, but every action is audited and needs MFA; can make mistakes |

## 4. Trust boundaries

| # | Boundary | What crosses it | Rule |
|---|---|---|---|
| TB-1 | Browser -> API | source (<= 100,000 characters), parameters, run requests | Existing auth, scopes, tenancy filters (`tenant_id` and `user_id` on every Lab read), rate limits, plan limits |
| TB-2 | API -> queue -> dispatcher | `lab_runs` rows | The dispatcher re-reads the version by id and checks owner, flag and quota at claim time. It never trusts the queued config alone. |
| TB-3 | Dispatcher <-> runner (UDS) | Requests: source, contexts, limits. Responses: framed results, logs, status. | **The main boundary.** Every response is untrusted: length-prefixed frames, a hard size cap before parsing, a strict parser, contract validation, and read timeouts. The socket directory is mounted only into the dispatcher and the runner. |
| TB-4 | Runner supervisor <-> per-run jail | stdin (request), stdout (result frame), stderr (logs) | The jail is the security boundary: namespaces, slot uid, cgroup, seccomp, read-only binds. The supervisor reads output as a capped stream. |
| TB-5 | Language worker <-> user code | the context object, the `atp.*` helpers | Defence in depth only (restricted builtins, `vm` realm, authorizer, curated environments). Never counted as a boundary. |
| TB-6 | Runner container <-> host kernel and resources | syscalls, CPU, memory, pids | gVisor (preferred) or a runner-only seccomp profile; `cap_drop: ALL`; `no-new-privileges`; container `mem_limit`, `cpus`, `pids_limit` |
| TB-7 | Results -> UI and downstream | rule names, details, numbers, logs | Rendered as plain text, never as HTML. Report and CSV exports neutralise formula prefixes. Never passed to the order path, alerts or proposals (A-7). |

**Entry points for X-1:**
- source text, parameter values and the strategy spec (timeframes, history length);
- run configuration (universe, timeframe, as-of);
- names and descriptions;
- the bytes their code writes to stdout and stderr;
- timing and cancellation.

The resource limits are **not** an entry point. They come from the plan and the runner configuration, never from
the author (observation O-3).

## 5. STRIDE

Evidence refers to the G-LAB checklist items in section 9.

| # | STRIDE | Threat | Boundary | ATP control | Evidence |
|---|---|---|---|---|---|
| S-1 | Spoofing | Run, read or publish another user's strategy by id | TB-1 | Every Lab query filters on `tenant_id` and `user_id` (404 otherwise); routes classified in `tenancy_route_table.py`; the dispatcher re-checks the owner at claim time | G-8 |
| S-2 | Spoofing | Code in a jail connects to the runner's UDS and submits its own runs without quota, or reads other runs' replies | TB-4 | The socket directory is not mounted into any jail. The jail's own network namespace blocks abstract Unix sockets. The supervisor serves only the dispatcher's connection. | G-6a, G-6b |
| S-3 | Spoofing | A result claims to belong to another run, symbol or time | TB-3 | A nonce per request; results are matched to the run id and to the exact symbol list the host sent; the host sets every timestamp and identity | G-7e |
| S-4 | Spoofing | A forged approval record or self-test result turns a code flag on | X-5, X-7 | The approval needs a SUPER_ADMIN with MFA and is audit-logged. It binds the image digest, seccomp hash and worker hash, so any mismatch voids it. CI runs the probe suite against the published digests, so the runner's own self-test is not the only proof. | G-12, G-13 |
| T-1 | Tampering | A published version is altered after review or a scan | TB-1 | No UPDATE path in code; a PostgreSQL `BEFORE UPDATE` trigger refuses changes to `lab_versions`; SHA-256 of source and compiled form | G-14 |
| T-2 | Tampering | One run changes another run's files, or the runtime used by later runs | TB-4 | Read-only binds of the runtime; a private tmpfs work directory per run, destroyed at the end; a distinct uid per slot; read-only container root | G-6c, G-8 (residue) |
| T-3 | Tampering | The runner image or a package is replaced upstream | X-6 | Base images pinned by digest; packages pinned to releases at least two weeks old; SBOM; image scan gate; the approval binds the digest | G-11 |
| T-4 | Tampering | Results forged by printing the result marker (AMW's `@@AMW@@` line, observation O-1) | TB-4 | The result travels as one framed message from the worker, separate from the log stream. The contract is checked whatever the source. Forging can only affect the author's own research output. | G-7e |
| R-1 | Repudiation | A user denies having run code that misbehaved | TB-1 | An audit row for every validate, try, publish, scan and backtest, with the source SHA-256, the image digest, the user, the tenant and the time | G-14 |
| R-2 | Repudiation | An operator denies turning a code flag on or approving a family | X-7 | `set_flag` and the approval write are audit-logged with user, MFA state and the bound digests | G-12 |
| I-1 | Information disclosure | Secrets read from the environment | TB-3, TB-4 | The runner's environment holds only the socket path. Each jail starts with an empty environment. No secret is mounted. | G-6b (`/proc/1/environ`, canary) |
| I-2 | Information disclosure | Files read outside the jail (`/etc/shadow`, application source, socket directory, runner configuration) | TB-4, TB-6 | The jail's root holds only read-only binds of its runtime. The runner image holds no application source or secrets. | G-6b |
| I-3 | Information disclosure | Another run's process, `/proc/<pid>/cwd`, environment or memory | TB-4 | A PID namespace with a fresh `/proc`; a distinct uid per slot; seccomp denies `ptrace` and `process_vm_*` | G-6d, G-8 |
| I-4 | Information disclosure | Another user's data reaches a context | TB-2 | Contexts are built per run from the requester's entitled data only (lake or their own session, BT1b). The dispatcher never batches two users' contexts in one call. | G-8, G-15 |
| I-5 | Information disclosure | Bulk export of licensed market data through results or logs | TB-7 | The result contract has no series field. Rule details are bounded; logs are capped (16 KB) and owner-only. Contexts carry only what the user may already chart. | G-15 |
| I-6 | Information disclosure | Error text leaks host paths, versions or other users' data | TB-3 | Errors are sanitised (<= 2,000 characters) and built by the host from a closed set of reasons, plus the user's own stderr tail | G-7e |
| I-7 | Information disclosure | Timing or cache side channels between runs | TB-6 | gVisor; concurrency 1 per runner on the 8 GB host (no co-resident runs); nothing secret is in the runner to leak | Residual risk RR-2 |
| D-1 | Denial of service | Fork bomb | TB-4 | Per-run `pids.max` (for example 32); container `pids_limit`; `cgroup.kill` at the end of the run | G-7a |
| D-2 | Denial of service | Memory bomb; Node heap growth | TB-4, TB-6 | Per-run `memory.max` and `memory.swap.max = 0`; Node `--max-old-space-size` under the same cgroup; container `mem_limit` (`MEM_LAB`) | G-7b |
| D-3 | Denial of service | CPU spin, long sleep, escaped child after `setsid` or double fork | TB-4 | Wall-clock and CPU timeouts; `cpu.max`; `cgroup.kill` ends the whole tree | G-7c |
| D-4 | Denial of service | Output flood, or one very long line without a newline, exhausts host memory | TB-4, TB-3 | Streaming read with a hard byte cap; kill on overflow ("output limit exceeded"); nothing is buffered beyond the cap; the dispatcher caps frame size | G-7d |
| D-5 | Denial of service | Crafted JSON (deep nesting, huge numbers, duplicate keys, very long strings) blows up the dispatcher's parser | TB-3 | Size cap before parse; nesting and string-length limits; duplicate keys refused; non-finite numbers refused; schema with no extra fields | G-7e |
| D-6 | Denial of service | Inode or tmpfs exhaustion | TB-4 | A tmpfs work directory with size and inode (`nr_inodes`) limits; `RLIMIT_FSIZE`; a read-only root | G-7f |
| D-7 | Denial of service | Queue flooding, or a burst of runs starving the trading worker or the API | TB-1, TB-6 | Per-user and global concurrency held in the database or Redis; a per-plan daily CPU budget; plan limits on symbols and runs; the runner's `cpus` limit; the research worker is a separate process; flag off cancels queued runs | G-9, G-10 |
| D-8 | Denial of service | A runner crash loop or a hung socket stalls the research worker's other jobs | TB-3 | Socket read timeouts; a heartbeat with stale runs marked `interrupted`; a failed self-test fails closed to "unavailable" without retry storms | G-13 |
| E-1 | Elevation of privilege | Container escape through a kernel bug | TB-6 | gVisor (`runsc`) where it installs; otherwise a runner-only seccomp allow-list; `cap_drop: ALL`; `no-new-privileges`; no setuid binaries in the image; never `privileged` or `CAP_SYS_ADMIN` | G-3, G-4, G-11 |
| E-2 | Elevation of privilege | Jail escape into the runner supervisor | TB-4 | User namespace and a slot uid different from the supervisor's; seccomp denies `mount`, `unshare`, `setns`, `bpf`, `keyctl`, `perf_event_open`, `userfaultfd`, `kexec*`, module and clock calls | G-4, G-5, G-6d |
| E-3 | Elevation of privilege | Language-guard bypass (Python object graph, JavaScript constructor chains, R aliasing, Lua `string.dump`, SQL `ATTACH`) | TB-5 | Guards are defence in depth. The jail still holds after a bypass, and the red-team suite runs every case against the jail with the guard **disabled** as well as enabled. | G-6e |
| E-4 | Elevation of privilege | User code reaches the order path | TB-7 | Code-mode results never create entry intents, Scan Then actions, auto-PAPER entries, proposals or alert rules (AMW-Q9). Code-mode versions cannot be deployed. PAPER only. | G-16 |
| E-5 | Elevation of privilege | A code flag is turned on without review, or stays on after the image changed | X-7 | `set_flag` refuses without a valid approval and has no override. The approval is voided by any digest change. | G-12 |
| E-6 | Elevation of privilege | Through the dispatcher: a compromised runner's frame exploits the host parser to reach database credentials | TB-3 | A minimal, strict frame parser in the dispatcher with fuzz tests; no deserialisation beyond JSON; no field is used as a path, SQL or command | G-7e |

## 6. The twelve AMW sandbox gaps and the ATP control that closes each

The gaps are from the port plan, section 3.4. They describe `src/lab/sandbox.py` in AMW v2.9.0.

| # | AMW gap | ATP control | Evidence |
|---|---|---|---|
| 1 | **Privilege.** Root, `CAP_SYS_ADMIN` and `apparmor:unconfined` are needed on the app and API containers, so the whole web application runs with a wider kernel surface. | User code never runs in the backend, worker or frontend containers. Only `lab-runner` runs it, as a non-root user with `cap_drop: ALL` and `no-new-privileges`. Under gVisor no host capability is involved. Without gVisor, a runner-only seccomp profile allows just the namespace calls the jail needs. Never `privileged`, never `CAP_SYS_ADMIN`. A CI check reads the compose files. | G-2, G-3, G-4 |
| 2 | **No PID namespace; one shared uid.** `/proc` shows every host process; a run can signal or trace another user's concurrent run and read its `/proc/<pid>/cwd` (R's `strategy.R` and `ctx_data.R`). | A PID namespace with a fresh `/proc` per run; a distinct uid per concurrent slot; seccomp denies `ptrace` and `process_vm_*`; concurrency 1 per runner on the 8 GB host | G-6d, G-8 |
| 3 | **No cgroup.** No `pids.max` or `RLIMIT_NPROC`, so a fork bomb works; memory is bounded only by `RLIMIT_AS`, which is not applied to Node. | cgroup v2 per run (`memory.max`, `memory.swap.max = 0`, `pids.max`, `cpu.max`) for every language, Node included; container `pids_limit` and `mem_limit` as a second net | G-7a, G-7b |
| 4 | **Escaped children survive.** A child that calls `setsid` or double-forks leaves the process group, so `killpg` misses it, and it keeps running as uid 65534 next to later runs. | `cgroup.kill` on timeout, cancel and normal end kills every process in the run's cgroup. The PID namespace dies with its init. The slot uid is reused only after the cgroup is empty. | G-7c |
| 5 | **No seccomp filter.** The full host-kernel syscall surface stays reachable. | gVisor's user-space kernel, or the runner seccomp profile; plus a per-jail seccomp-bpf allow-list in both cases | G-4, G-5 |
| 6 | **Filesystem.** Only the temporary directories are replaced. The rest of the root filesystem is visible where file modes allow (application source, packages, `/etc`). | The jail's root is built from read-only binds of exactly the runtime it needs, plus a private tmpfs. No host root filesystem; minimal `/etc`; `/dev` limited to `null`, `zero` and `urandom`. The runner image holds no application source or secrets. | G-6b |
| 7 | **Unbounded output buffering.** `communicate()` reads everything into host memory before truncating. | The supervisor reads stdout and stderr as a stream with a hard byte cap and kills the run on overflow. The dispatcher caps frame size before parsing. | G-7d, G-7e |
| 8 | **Quotas per process, in memory.** The dashboard and the API each keep their own counters. | Global and per-user concurrency and the per-user daily CPU seconds are held in the database (or Redis) and checked at claim time by the single dispatcher | G-9 |
| 9 | **Self-test cached once per process** (`lru_cache`), never repeated after an environment or image change. | The probe suite runs at runner start, every hour and after any image change; the dispatcher stores the result with the image digest; a stale or failed result fails closed; CI runs the same suite against the published digests | G-13 |
| 10 | **Unsandboxed mode** on Windows or with `AMW_ALLOW_UNSANDBOXED=1`. | None exists. No code path runs user code outside the runner on any platform; a missing runner means "unavailable". A test asserts there is no such switch. | G-2, G-13 |
| 11 | **Bypassable deny-list language guards** (for example R's `\bsystem2?\s*\(` misses `f <- system; f("...")`). | Guards are defence in depth only. R gets a curated environment (parent is a function allow-list, not `baseenv()`) and JSON contexts. The red-team suite also runs with guards disabled, to prove the jail alone holds. | G-6e |
| 12 | **World-writable (1777) work directory; `RLIMIT_FSIZE=0` does not stop directories or inodes.** | A private tmpfs per run, owned by the slot uid (mode 0700), with size and `nr_inodes` limits; a read-only root elsewhere | G-6c, G-7f |

### Further observations from the reference code

These were found while reading the reference code for this slice. They are not in the plan's list of twelve.

| # | Observation | ATP control |
|---|---|---|
| O-1 | `engine._parse_worker` takes the last stdout line that starts with `@@AMW@@` as the result. Where user code can write to the same stdout, it can forge that line. | One framed result message per call, on a stream separate from logs; contract validation; results bound to the run nonce and the symbols the host sent (S-3, T-4) |
| O-2 | R contexts are serialised as R **source** (`_r_lit`), and dict keys are put inside backticks without escaping. A key holding a backtick would break into R code. Keys are host-built today, so the risk is low, but data must never become code. | Contexts go to every language as JSON data, parsed inside the runtime (plan 3.4, layer 3) |
| O-3 | Resource limits are part of the author's strategy spec (`ResourceLimits`: timeout up to 120 s, memory up to 2,048 MB, output up to 4 MB per context). The author chooses their own limits within high ceilings. | Limits come from the plan and the runner configuration. The spec cannot raise them. |
| O-4 | The TypeScript compile step runs with no memory limit (`mem_mb=None`) and at least a 20 s timeout. | The compile step is a jailed run like any other, with the same cgroup limits |

## 7. Per-run limits and the CPU budget (provisional, measured in AMW-21 and G-10)

| Limit | Value | Where enforced |
|---|---|---|
| Wall-clock per runner call | 15 s (R: 30 s) | supervisor timer, then `cgroup.kill` |
| CPU time per runner call | 10 s (R: 20 s) | `cpu.max` + `RLIMIT_CPU` |
| Memory per run | 256 MB (R: 768 MB), swap 0 | `memory.max`, `memory.swap.max` |
| Processes per run | 32 | `pids.max` |
| Open files | 64 | `RLIMIT_NOFILE` |
| Work directory | 4 MB tmpfs, 256 inodes | tmpfs `size`, `nr_inodes` |
| Output | result frame 256 KB; logs 16 KB (stored), stderr read cap 64 KB | supervisor stream cap, dispatcher frame cap |
| Source | 100,000 characters | API (TB-1) and runner |
| Contexts per call | 50 (R: 10) | dispatcher |
| Try run | at most 5 symbols, drafts only | API |
| Concurrency | 1 run per runner on the 8 GB host (global), 1 per user | database or Redis, at claim time |
| Runner container | `cpus: 0.5`, `MEM_LAB=512m` (R: `768m`), `pids_limit: 128` | compose |

**CPU budget per plan** (`lab_cpu_seconds_per_day`, AMW-1; open question LAB-1):

| Plan | Code-mode CPU seconds per user per IST day | Reason |
|---|---|---|
| Free | 0 (host modes only) | Sign-up is open, so a free account is the cheapest attacker. Host modes run no user code. |
| Pro | 900 | About 90 full 10 s calls a day; with 50 contexts a call, that is a few universe scans |
| Business | 3,600 | Four times Pro |

The runner's `cpus: 0.5` caps the host-wide total at 43,200 CPU seconds a day, whatever the plans add up to. The
trading worker keeps at least 1.5 vCPU.

## 8. Residual risks

| # | Risk | Why it remains | Mitigation / acceptance |
|---|---|---|---|
| RR-1 | A kernel or gVisor bug reachable through the allowed syscalls | No isolation is perfect | gVisor where available; a minimal seccomp allow-list; prompt image and host patching; the runner holds nothing of value; accepted by the owner at G-LAB |
| RR-2 | Timing and cache side channels (CPU caches, Spectre-class) between runs, or between a run and host processes | A shared 2 vCPU host | Concurrency 1; no secrets in the runner; host mitigations on. Accepted. |
| RR-3 | A compromised runner (X-5) forges its own self-test until the next CI probe run or image change | The probe runs inside the thing it tests | CI probes the same digests; the approval binds digests; the dispatcher treats frames as untrusted. Accepted. |
| RR-4 | Without gVisor, the seccomp profile must allow the namespace calls the jail needs, so the runner container has a wider surface than the others | AMW-Q3: gVisor may not install on Hostinger KVM 2 | The profile applies to this container only; it is reviewed in G-4. gVisor stays the preferred option. |
| RR-5 | Language guards are bypassable | Deny-lists and realms are not boundaries | Recorded as defence in depth; the suite runs with guards off |
| RR-6 | An operator with database or host access bypasses `set_flag` by editing the platform control directly | Operators are trusted (X-7) | Audit logs. Before every run the dispatcher compares the approval with the digests the runner reports at its handshake and in its latest self-test, so a hand-edited flag still fails closed on a mismatch. |
| RR-7 | Results are user-authored and can be misleading (look-ahead is impossible, but bad logic is not) | Research output by design | Labelled as user-supplied research; never trades; the disclaimer is shown |
| RR-8 | R's memory use leaves the 8 GB host with about 0.6 GB of headroom | Host size | R comes last, runs alone, and only after measurement or on a larger host (AMW-Q6) |
| RR-9 | Abuse through many free or throwaway accounts | Open sign-up | Free plan has no code-mode CPU; plan, rate and global limits; the flag can be scoped to tenants |

## 9. Gate G-LAB - the security review checklist

No code-family flag (`strategy_lab_python`, `strategy_lab_sql_lua`, `strategy_lab_js`, `strategy_lab_r`) can be
turned on until every item below has its evidence attached to the review record. `set_flag` enforces the result, not
the list: it needs the approval record, which may only be written once this list is complete.

For each item, **Evidence** is the artefact the reviewer reads and **Pass** is the condition that must hold. "CI"
means the test runs in CI against the real runner image digests. "Host" means it was also run once on the production
host profile (2 vCPU / 8 GB) and the output was attached.

**Records and design**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-1 | ADR-0027 and this threat model | ADR status, review notes | ADR-0027 **accepted** by the owner. Every STRIDE row has a control and an evidence item, or a residual-risk entry the owner has accepted. |

**Container and isolation configuration**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-2 | Runner container configuration | `docker-compose*.yml`, plus a CI test that parses them | `lab-runner`: `network_mode: none`, `read_only: true`, non-root `user`, `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit`, `mem_limit`, `cpus`; no `env_file`, and no environment entry except the socket path; no database or secrets volume. Backend, worker, research worker and frontend: no new capability, `privileged`, `security_opt` or device. No code path or switch runs user code outside the runner. |
| G-3 | gVisor profile, if used | `runsc --version`, the Docker `daemon.json` runtime entry, `runsc` flags (`--network=none`), and the probe suite output under `runsc` on the host | `runtime: runsc` on `lab-runner` only. The probe suite passes under it on the host. |
| G-4 | seccomp profile, if gVisor is not used (and the per-jail filter in every case) | The profile JSON files, their SHA-256 hashes (bound into the approval), and a diff against Docker's default profile with every added syscall justified | The container profile adds only the namespace and cgroup calls the jail tool needs. The per-jail allow-list denies `ptrace`, `process_vm_*`, `mount`, `umount2`, `unshare`, `setns`, `pivot_root`, `keyctl`, `add_key`, `bpf`, `perf_event_open`, `userfaultfd`, `kexec_*`, `*_module`, `clock_settime`, `settimeofday`, `socket` (except `AF_UNIX` socketpairs if a worker needs them), `io_uring_*`. A test calls each denied syscall from the jail and expects `EPERM` or `ENOSYS`. |
| G-5 | Per-run jail configuration | The jail tool version (pinned), its configuration file, and one dumped jail's `/proc/self/status`, `uid_map`, mount table and cgroup files | New user, PID, mount, network, IPC and UTS namespaces. `CapEff` is 0. The uid is the slot's own. The mount table shows only read-only runtime binds and the tmpfs work directory. The cgroup has `memory.max`, `memory.swap.max = 0`, `pids.max` and `cpu.max` set to section 7's values. |

**Escape tests** (the red-team suite; CI and host; each case is one test with its expected outcome)

| # | Item | Cases | Pass |
|---|---|---|---|
| G-6a | Network egress | TCP connect to an outside IP and to the Docker host gateway; UDP send; DNS lookup; ICMP; IPv6; abstract Unix socket connect; netlink socket; connect to the runner's UDS path | Every attempt fails; no packet leaves (checked from the host with a capture on the runner's interfaces: there are none) |
| G-6b | File reads outside the jail | `/etc/shadow`, `/etc/passwd` of the runner, `/proc/1/environ`, `/proc/1/cmdline`, `/proc/self/mountinfo` looking for host paths, the runner's configuration, the socket directory, any application source path, `/sys/fs/cgroup` above the run's own cgroup, the Docker socket | Not present or `EACCES`. The environment canary set in the supervisor is never visible. |
| G-6c | Writes outside the work directory | Write into the runtime binds, `/tmp` outside the jail, `/dev/shm`, `/proc/sys`, the cgroup files | All fail; the work directory is the only writable path |
| G-6d | `/proc` and other runs | Two concurrent runs (concurrency raised to 2 for the test, with different users and tenants): list `/proc`; read the other's `/proc/<pid>/cwd`, `environ` and `maps`; `kill` and `ptrace` the other; `process_vm_readv` | Each run sees only its own PIDs. Every cross-run call fails. |
| G-6e | Per language, with the guard enabled **and** disabled | Python: `__subclasses__`, frame and `gc` walking, `__builtins__` recovery, `importlib`, `ctypes`. JavaScript / TypeScript: `constructor.constructor`, `Proxy`, `import()`, `WebAssembly`, `process.binding`, `eval` and `Function` from strings. R: aliasing (`f <- system`), backtick calls, `.Internal`, `.Call`, `do.call`, environment walking, `get` / `match.fun`. Lua: `string.dump`, `load`, metatables, `debug`, `collectgarbage`. SQL: `ATTACH`, `load_extension`, `PRAGMA`, recursive-CTE blow-ups, `randomblob` size | Guard on: refused with a clear error. Guard off: whatever the code reaches, G-6a to G-6d still hold. |
| G-7a | Fork bomb | `while True: os.fork()` in Python, and the equivalents in Node, R and Lua where they exist | Contained by `pids.max`; host PID count unchanged; the next run starts normally |
| G-7b | Memory bomb | Grow an allocation without limit (Python list, Node array and Buffer, R vector, Lua table) | The run is OOM-killed inside its own cgroup and reported "memory limit exceeded". No other container is touched (`docker stats` and the kernel OOM log are attached). |
| G-7c | Timeout kills the whole process tree | CPU spin; `sleep` past the limit; a child after `setsid`; a double-forked daemon; a child that ignores `SIGTERM`; cancel mid-run | Every process ends within 1 s of the deadline or the cancel (`cgroup.kill`). The cgroup is empty before the slot is reused. No process with the slot uid survives (checked from the supervisor). |
| G-7d | Output flood | Gigabytes to stdout until the timeout; the same on stderr; one line with no newline; millions of tiny writes; output after the result frame | Killed at the cap with "output limit exceeded". Host RSS of the supervisor and the dispatcher stays within a fixed bound (measured). |
| G-7e | Crafted JSON | Oversized frame; nesting depth 10,000; duplicate keys; `NaN`, `Infinity`, `1e400`; wrong types; extra fields; lone UTF-16 surrogates and control characters; strings over the limit; results for a symbol not sent; a missing or wrong nonce; timestamps in the output; a stop on the wrong side; a forged `@@AMW@@`-style line in the logs | Each is refused by the dispatcher with a sanitised error and no partial store. A fuzz run of the frame parser (at least 1 million cases) shows no crash and no unbounded memory. |
| G-7f | Inode and disk exhaustion | Create files and directories until failure; large `ftruncate`; sparse files | Stops at the tmpfs `size` and `nr_inodes` limits; the host disk is untouched |

**Isolation between tenants and users**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-8 | Per-tenant isolation tests | Tests with two tenants and two users each | User B gets 404 on user A's strategies, versions, runs, results and logs, whether A is in the same tenant or another one. All Lab routes are classified in `tenancy_route_table.py`. A run's contexts hold only the requester's entitled data. The dispatcher never puts two users' contexts in one call. Residue: run A writes to its work directory and run B, in the same slot afterwards, finds nothing. One user's quota cannot be spent by a teammate. |

**Resources and the trading host**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-9 | Quotas | Tests | Per-user and global concurrency, daily CPU seconds, source size, contexts per call, symbols per scan and plan limits are each refused at the limit. The counters are in the database or Redis, and two API processes share them. |
| G-10 | CPU budget per plan and load test | `lab_cpu_seconds_per_day` values in `backend/app/plans/registry.py` (section 7, with LAB-1 answered); a load test on the 8 GB profile; `docs/BENCHMARKS.md` entries | The budget is enforced (a test spends it and is refused). Under a burst of code runs at the configured concurrency, the API's p95 latency moves by no more than 10 % and the trading worker's cycle time by no more than 5 %. Peak RSS per family and image sizes are recorded. `MEM_LAB` is set, and the sum of container limits stays under about 7 GB. |

**Supply chain**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-11 | Image SBOM and pinning | SBOM (SPDX or CycloneDX) per runner image, stored with the review; the image scan report; Dockerfiles | Base images pinned by digest. Node, TypeScript, R, lupa, sqlglot and the jail tool pinned to releases at least two weeks old. No high or critical finding without a recorded exception. No setuid or setgid binary in the image. No compiler or package manager in the final stage. |

**Gate mechanics, self-test and result use**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-12 | The flag switch | Tests on `set_flag` | Turning a code-family flag on is refused when `strategy_lab` is off, when the self-test is stale or failed, when the approval is missing, and when the approval's image digest, seccomp hash or worker hash differs from the live one. There is no override argument. Each refusal is audit-logged. Turning the flag off cancels the family's queued runs. |
| G-13 | Self-test and fail closed | Tests; the stored self-test record; the operator notification | The probe suite runs at start, hourly and after an image change. A stale or failed result turns every code mode "unavailable" on the next request, with one operator notification. CI probes the published digests. A missing runner means "unavailable", never a fallback. |
| G-14 | Audit and immutability | Tests | Every validate, try, publish, scan and backtest writes an audit row with the source SHA-256 and the image digest. An UPDATE on `lab_versions` is refused by the trigger. |
| G-15 | Data minimisation | Tests | Contexts carry only completed bars, cut at the decision bar (Z10-a), from the requester's entitled sources. No key, token or account field can reach a context: a canary secret planted in every store never appears in any context. Logs are capped, owner-only, sanitised, and rendered as plain text. |
| G-16 | Results never trade | A test over every producer and consumer | Code-mode results never reach `execution_core` intents, Scan Then, auto PAPER, proposals or alert rules. A code-mode version cannot be deployed. |

**Operations and sign-off**

| # | Item | Evidence | Pass |
|---|---|---|---|
| G-17 | Kill path rehearsal | A runbook entry and its rehearsal log on the host | Family flag off, then `docker compose --profile lab stop`. Trading, exits and alerts are unaffected during the rehearsal (worker cycle log attached). |
| G-18 | User-facing text | The page copy and terms | The page says what isolation means, the limits, that results are research only and never trade, and the terms for uploaded code. English only, and it passes the wording check. |
| G-19 | Sign-off and approval record | The review record | An independent reviewer (not the author) and the owner sign off. The `lab_security_review` record is written per family by a SUPER_ADMIN with MFA, bound to the commit, image digest, seccomp hash and worker hash. |

**Per family.**
- Python is reviewed first (AMW-22).
- Each later family (SQL and Lua, JavaScript and TypeScript, R) repeats these before its own flag:
  - G-4 and G-5 (its profile and jail);
  - G-6e (its language cases);
  - G-7 (with its runtime);
  - G-10 (its memory and CPU);
  - G-11 (its image);
  - G-19.

**Re-review.** Any change to a runner image, the seccomp profile, the jail configuration or a worker script voids
that family's approval automatically, because the digest or hash no longer matches. G-4 to G-7, G-11 and G-19 are
then repeated for that family.

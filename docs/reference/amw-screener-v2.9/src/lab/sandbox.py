"""Process sandbox for untrusted strategy code (all languages except visual/pine, which are never executed as code).

Linux (verified by `self_test`):
  * `unshare -n`  -> new empty network namespace (no network at all)
  * `setpriv`     -> drop to uid/gid 65534 (nobody), no supplementary groups, no_new_privs
  * rlimits       -> CPU seconds, address space (non-Node), file size 0 (no writes), open files, core 0
  * empty environment (no secrets), private temp working directory (deleted afterwards)
  * wall-clock timeout + process-group kill, bounded stdout/stderr
  * global concurrency cap (AMW_LAB_MAX_CONCURRENCY) and per-user cap (AMW_LAB_MAX_PER_USER)
Other platforms (e.g. Windows): these controls are NOT available -> sandbox NOT verified and code execution is
disabled unless AMW_ALLOW_UNSANDBOXED=1 (single-user, own code only; documented as unsafe). Never enable on a server.
"""
from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

MAX_CONCURRENCY = int(os.environ.get("AMW_LAB_MAX_CONCURRENCY", "2"))
MAX_PER_USER = int(os.environ.get("AMW_LAB_MAX_PER_USER", "1"))
_global = threading.BoundedSemaphore(MAX_CONCURRENCY)
_per_user: dict[str, int] = defaultdict(int)
_lock = threading.Lock()


class QuotaExceeded(RuntimeError):
    pass


@dataclass
class ProcResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    elapsed_s: float
    truncated: bool
    sandboxed: bool


def _linux_tools() -> bool:
    return platform.system() == "Linux" and shutil.which("unshare") is not None and shutil.which("setpriv") is not None \
        and hasattr(os, "geteuid") and os.geteuid() == 0


def _preexec(cpu_s: int, mem_mb: int | None):
    def f():
        import resource
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
        if mem_mb:
            resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 2**20, mem_mb * 2**20))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return f


def run(cmd: list[str], stdin: str, timeout_s: float = 10, mem_mb: int | None = 256, max_output_kb: int = 512,
        user_key: str = "local", force_unsandboxed: bool = False, extra_files: dict[str, str] | None = None,
        cancel=None) -> ProcResult:
    """Run `cmd` isolated. `extra_files` are written (read-only, world-readable) into the private work dir first."""
    sandbox = _linux_tools() and not force_unsandboxed
    if not sandbox and not (force_unsandboxed or os.environ.get("AMW_ALLOW_UNSANDBOXED") == "1"):
        raise PermissionError("sandbox unavailable on this platform; code execution disabled (see docs/STRATEGY_LAB.md)")
    with _lock:
        if _per_user[user_key] >= MAX_PER_USER:
            raise QuotaExceeded(f"concurrency quota: {MAX_PER_USER} running execution(s) per user")
        _per_user[user_key] += 1
    try:
        if not _global.acquire(timeout=timeout_s):
            raise QuotaExceeded("lab executor busy")
        try:
            return _run(cmd, stdin, timeout_s, mem_mb, max_output_kb, sandbox, extra_files or {}, cancel)
        finally:
            _global.release()
    finally:
        with _lock:
            _per_user[user_key] -= 1


def _work_base() -> str:
    from ..config import ROOT
    base = ROOT / ".sandbox"
    base.mkdir(exist_ok=True)
    try:
        os.chmod(base, 0o711)        # traversable, not listable: sandbox can only reach its own random work dir
    except OSError:
        pass
    return str(base)


MOUNT_SCRIPT = ("mount -t tmpfs -o size=1m,mode=1777 tmpfs /tmp && "
                "(mount -t tmpfs -o size=1m,mode=1777 tmpfs /var/tmp 2>/dev/null; mount -t tmpfs -o size=1m,mode=1777 tmpfs /dev/shm 2>/dev/null; true) && "
                'exec setpriv --reuid=65534 --regid=65534 --clear-groups --no-new-privs -- "$@"')


def _run(cmd, stdin, timeout_s, mem_mb, max_output_kb, sandbox, extra_files, cancel=None) -> ProcResult:
    work = tempfile.mkdtemp(prefix="amwlab_", dir=_work_base() if sandbox else None)
    try:
        for name, content in extra_files.items():
            p = os.path.join(work, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(p, 0o444)
        os.chmod(work, 0o1777)   # sandbox user may create temp dirs (R needs one); RLIMIT_FSIZE=0 blocks file content
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": work, "TMPDIR": work, "PYTHONDONTWRITEBYTECODE": "1"}
        full = (["unshare", "-n", "-m", "--propagation", "private", "sh", "-c", MOUNT_SCRIPT, "amw-sandbox"] + cmd
                if sandbox else cmd)
        t0 = time.perf_counter()
        kw = {}
        if platform.system() != "Windows":
            kw["preexec_fn"] = _preexec(max(1, int(timeout_s)), mem_mb)
        else:
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        proc = subprocess.Popen(full, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=work,
                                env=env if platform.system() != "Windows" else {**env, "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
                                **kw)
        timed_out = cancelled = False
        deadline = time.perf_counter() + timeout_s
        data = stdin.encode()
        while True:
            try:
                out, err = proc.communicate(data, timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                data = None
                cancelled = bool(cancel is not None and cancel.is_set())
                if time.perf_counter() < deadline and not cancelled:
                    continue
            timed_out = not cancelled
            try:
                if platform.system() != "Windows":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except ProcessLookupError:
                pass
            out, err = proc.communicate()
            if cancelled:
                err = (err or b"") + b"\ncancelled by user"
            break
        lim = max_output_kb * 1024
        trunc = len(out) > lim or len(err) > lim
        return ProcResult(None if timed_out else proc.returncode, out[:lim].decode("utf-8", "replace"),
                          err[:lim].decode("utf-8", "replace"), timed_out, round(time.perf_counter() - t0, 3), trunc, sandbox)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _app_data():
    from ..config import ROOT
    return ROOT / "data"


def protect_app_dirs() -> None:
    """Make app data/cache unreadable for the sandbox user (0700, owned by the app user)."""
    from ..config import ROOT
    for d in (ROOT / "data", ROOT / ".cache"):
        d.mkdir(exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


@lru_cache(maxsize=1)
def self_test() -> dict:
    """Verify isolation empirically: network, secrets, filesystem writes, privilege."""
    protect_app_dirs()
    if _linux_tools():
        import stat
        import sys as _sys
        from ..config import ROOT
        for path in (_sys.executable, str(ROOT)):
            real = os.path.realpath(path)
            if real.startswith(("/tmp/", "/var/tmp/", "/dev/shm/")):
                return {"verified": False, "reason": f"{real} is under a directory the sandbox replaces with a private tmpfs; "
                        "install the app and its virtualenv elsewhere (e.g. /opt/amw)"}
            cur = os.path.dirname(real)
            while cur not in ("/", ""):
                if not os.stat(cur).st_mode & stat.S_IXOTH:
                    return {"verified": False, "reason": f"{cur} is not traversable by the sandbox user; install the app and its "
                            "virtualenv in a world-readable location (e.g. /opt/amw), not under /root"}
                cur = os.path.dirname(cur)
    if not _linux_tools():
        return {"verified": False, "reason": f"{platform.system()}: unshare/setpriv/root not available"}
    os.environ.setdefault("AMW_SANDBOX_CANARY", "secret-canary")
    import secrets as _s
    marker = f"amw_escape_{_s.token_hex(6)}"
    probe = (
        "import os,socket,json\n"
        "r={}\n"
        "r['uid']=os.getuid()\n"
        "r['secret_visible']='AMW_SANDBOX_CANARY' in os.environ\n"
        "try:\n socket.create_connection(('1.1.1.1',53),timeout=2); r['network']=True\n"
        "except Exception: r['network']=False\n"
        "try:\n f=open('w.txt','w'); f.write('x'*10); f.flush(); f.close(); r['write']=os.path.getsize('w.txt')>0\n"
        "except Exception: r['write']=False\n"
        f"for p in ('/tmp/{marker}', '/var/tmp/{marker}', '/dev/shm/{marker}'):\n"
        "  try:\n   open(p,'w').close()\n  except Exception: pass\n"
        "try:\n open('/root/.bashrc').read(); r['read_root']=True\n"
        "except Exception: r['read_root']=False\n"
        f"try:\n os.listdir({str(_app_data())!r}); r['read_app_data']=True\n"
        "except Exception: r['read_app_data']=False\n"
        "print(json.dumps(r))\n")
    try:
        import json
        import sys
        res = _run([sys.executable, "-I", "-B", "-c", probe], "", 10, 256, 64, True, {})
        lines = res.stdout.strip().splitlines()
        if not lines:
            return {"verified": False, "reason": f"sandbox could not start (missing privileges/namespaces?): {res.stderr.strip()[-200:]}"}
        r = json.loads(lines[-1])
        escaped = any(os.path.exists(f"{d}/{marker}") for d in ("/tmp", "/var/tmp", "/dev/shm"))
        r["host_file_escape"] = escaped
        ok = (r["uid"] == 65534 and not r["secret_visible"] and not r["network"] and not r["write"] and not r["read_root"]
              and not r["read_app_data"] and not escaped)
        return {"verified": ok, "checks": r}
    except Exception as e:  # noqa: BLE001
        return {"verified": False, "reason": f"self-test failed: {type(e).__name__}: {e}"}


def sandbox_status() -> dict:
    st = self_test()
    return {**st, "platform": platform.system(), "unsandboxed_allowed": os.environ.get("AMW_ALLOW_UNSANDBOXED") == "1",
            "max_concurrency": MAX_CONCURRENCY, "max_per_user": MAX_PER_USER}

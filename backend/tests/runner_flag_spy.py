"""Runner S4c: records every feature flag a runner loop job's code asks for (tests/test_runner_s4c_state.py, test 4).

Usage: python tests/runner_flag_spy.py <job.py> <runner folder>. The runner process is prepared first (runner mode, the
folder's SQLite file, no Redis - as the job itself does), then `platform.controls`' flag reads are wrapped to record the
names asked, and the job runs unchanged. Prints one line: FLAGS_ASKED <json list>.
"""
import atexit
import json
import runpy
import sys
from pathlib import Path

from app.runner.__main__ import prepare_process
from app.runner.settings import RunnerSettings

JOB, HOME = sys.argv[1], sys.argv[2]
prepare_process(RunnerSettings.load(Path(HOME)))

import app.platform.controls as controls  # noqa: E402 - after the runner process is prepared

asked: set[str] = set()


class _Recording(dict):
    def get(self, key, *default):                      # type: ignore[no-untyped-def]
        asked.add(key)
        return super().get(key, *default)

    def __getitem__(self, key):                        # type: ignore[no-untyped-def]
        asked.add(key)
        return super().__getitem__(key)


_flags, _enabled, _for_tenant = controls.feature_flags, controls.flag_enabled, controls.features_for_tenant


async def feature_flags(session):                      # type: ignore[no-untyped-def]
    return _Recording(await _flags(session))


async def flag_enabled(session, name, tenant_id=None):  # type: ignore[no-untyped-def]
    asked.add(name)
    return await _enabled(session, name, tenant_id)


async def features_for_tenant(session, tenant_id):     # type: ignore[no-untyped-def]
    return _Recording(await _for_tenant(session, tenant_id))


controls.feature_flags, controls.flag_enabled, controls.features_for_tenant = feature_flags, flag_enabled, features_for_tenant
atexit.register(lambda: print("FLAGS_ASKED " + json.dumps(sorted(asked))))
sys.argv = [JOB, HOME]
runpy.run_path(JOB, run_name="__main__")

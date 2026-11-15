"""Runner S4c (docs/design/USER_RUNNER.md, "S4c results"): ATP's state for the runner, in the signed heartbeat answer.

1. ATP's side: the heartbeat answer carries a state signed with ATP's command key under its own scheme - `live_open` for
   HIS organisation by the one rule (`live_switch.live_allowed`, B1 when merged), its plain reason when closed, his
   organisation's answer for each flag the runner's pipeline reads, the F&O ban check setting and his plan's `runner`
   field (None: plans carry none today);
2. the runner verifies it (ATP's pinned key, this device, this user, newer than the last; a command signature or a forged
   / tampered / replayed state is refused) and writes the flags into its local store, so the pipeline reads ATP's answer;
3. LIVE on the runner: open only when ATP's fresh state says so for his organisation AND his local LIVE file AND the
   deployment's own LIVE choice; stale past his grace, closed, another organisation or never received -> a plain reason;
   the PAPER-only lock the pipeline and the broker chokepoint read follows the same state; PAPER and exits never read it;
4. the flag list: every flag the runner loop's own code asks for (recorded while the S4b loop job runs) is either sent
   or listed as not sent with its reason.
Generic made-up numbers only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core import config, runtime_mode
from app.db.models import StrategyDeploymentRecord, TradeRecord, User
from app.platform import controls, live_switch, phase0_lock
from app.runner import link as runner_link
from app.runner.loop import LIVE_NEEDS_BROKER
from app.runner_link import service, signing
from tests.test_runner_s4_link import NOW, _flags_on, _run, _runner, _saved_rules, _set_flags  # noqa: F401 - autouse fixture

BACKEND = Path(__file__).resolve().parents[1]

# Flags the runner loop's code reads that ATP does not send, each with its reason (test 4 keeps this list honest).
NOT_SENT = {
    "phase0_paper_only": "derived from ATP's live_open (the PAPER-only lock follows ATP's signed state on the runner)",
    "alerts_reliable_outbox": "ATP's alert delivery outbox; the runner's notifications stay in its own store",
}


@pytest.fixture()
def atp_live(monkeypatch):
    """ATP's server half answers LIVE open for the given organisations (the real rule is live_switch.live_allowed; in this
    one-process test the runner half's lock seam is on, so ATP's half is pinned here)."""
    open_for: set[int] = set()

    async def allowed(session, tenant_id=None):                 # type: ignore[no-untyped-def]
        return tenant_id in open_for
    monkeypatch.setattr(live_switch, "live_allowed", allowed)
    yield open_for
    runtime_mode.set_live_open(())


def _state(runner) -> dict:
    return runner.link.atp_state


# --- 1. ATP's side ----------------------------------------------------------------------------------------------------

def test_the_heartbeat_answer_carries_a_signed_state_for_his_organisation(tmp_path, atp_live):
    headers, me, settings, store, pairing, runner, _ = _runner(tmp_path, "rs4c-state@example.com")
    _set_flags(smart_execution=True, risk_options=False)
    _run(runner.run_cycle(now=NOW))
    state = _state(runner)
    assert state["kind"] == "state" and state["device_id"] == pairing.device_id and state["user_ref"] == me["id"]
    assert state["tenant_ref"] == me["tenant_id"]
    assert state["live_open"] is False and state["live_reason"] == live_switch.LIVE_OFF          # closed: the plain reason
    assert set(state["flags"]) == set(service.RUNNER_FLAGS)
    assert state["flags"]["smart_execution"] is True and state["flags"]["risk_options"] is False
    assert state["fo_ban_check"] is config.FO_BAN_CHECK_ENABLED and state["plan_runner"] is None
    # The same answer, read straight from ATP: signed under the state scheme with ATP's command key (not as a command).
    async def answer():
        from tests.test_auth_api import _session_factory
        async with _session_factory() as session:
            device = await service.own_device(session, await session.get(User, me["id"]), pairing.device_id)
            return await service.runner_state(session, device, NOW)
    signed = _run(answer())
    key = service.command_public_key()
    assert signing.verify(key, signed["signature"], signing.state_message(signed["payload"]))
    assert not signing.verify(key, signed["signature"], signing.command_message(signed["payload"]))
    # Never an admin kill switch or a maintenance block in it (rule R-1): only the listed flags.
    assert "maintenance" not in json.dumps(signed["payload"]) and "kill" not in json.dumps(signed["payload"])

    atp_live.add(me["tenant_id"])
    _run(runner.run_cycle(now=NOW + timedelta(seconds=61)))
    assert _state(runner)["live_open"] is True and _state(runner)["live_reason"] is None
    assert "LIVE open for your organisation" in settings.link_log_path.read_text()


# --- 2. the runner's checks and the local flags ---------------------------------------------------------------------------

def test_the_runner_refuses_a_state_that_is_not_atps_own_for_this_device(tmp_path):
    _, me, _, _, pairing, _, _ = _runner(tmp_path, "rs4c-forged@example.com")
    good = {"v": 1, "kind": "state", "device_id": pairing.device_id, "user_ref": me["id"], "tenant_ref": me["tenant_id"],
            "issued_at": NOW.isoformat(), "live_open": True, "live_reason": None, "flags": {"smart_execution": True},
            "fo_ban_check": False, "plan_runner": None}

    def st(payload, key=None, message=signing.state_message):
        return {"payload": payload, "signature": signing.sign(key or service._command_key(), message(payload))}
    assert runner_link.verify_state(st(good), pairing, None) == (good, None)
    refusals = {
        "foreign key": runner_link.verify_state(st(good, Ed25519PrivateKey.generate()), pairing, None),
        "a command's signature": runner_link.verify_state(st(good, message=signing.command_message), pairing, None),
        "other device": runner_link.verify_state(st({**good, "device_id": "0" * 32}), pairing, None),
        "other user": runner_link.verify_state(st({**good, "user_ref": me["id"] + 1}), pairing, None),
        "tampered": runner_link.verify_state({**st(good), "payload": {**good, "live_open": False}}, pairing, None),
        "replay": runner_link.verify_state(st(good), pairing, NOW.isoformat()),
        "no time zone": runner_link.verify_state(st({**good, "issued_at": NOW.replace(tzinfo=None).isoformat()}), pairing, None),
        "not a flag answer": runner_link.verify_state(st({**good, "flags": {"smart_execution": "on"}}), pairing, None),
    }
    for name, (payload, reason) in refusals.items():
        assert payload is None and reason, name


def test_his_organisations_flags_land_in_the_local_store_and_the_pipeline_reads_them(tmp_path, atp_live):
    _, me, _, store, _, runner, _ = _runner(tmp_path, "rs4c-flags@example.com")
    _set_flags(smart_execution=True, approval_only_entries=False)
    _run(runner.run_cycle(now=NOW))

    async def local(name):
        async with store.sessions() as session:
            return await controls.flag_enabled(session, name, me["tenant_id"])
    assert _run(local("smart_execution")) is True and _run(local("approval_only_entries")) is False
    _set_flags(smart_execution=False)                                     # the owner changes it in ATP
    _run(runner.run_cycle(now=NOW + timedelta(seconds=61)))
    assert _run(local("smart_execution")) is False                        # the next answer brings it


def test_the_fo_ban_check_follows_atps_setting(tmp_path, atp_live, monkeypatch):
    _, _, _, _, _, runner, _ = _runner(tmp_path, "rs4c-ban@example.com")
    monkeypatch.setattr(config, "FO_BAN_CHECK_ENABLED", True)             # ATP's setting (one process: the same module)
    _run(runner.run_cycle(now=NOW))
    assert _state(runner)["fo_ban_check"] is True
    monkeypatch.setattr(config, "FO_BAN_CHECK_ENABLED", False)
    _run(runner.run_cycle(now=NOW + timedelta(seconds=61)))
    assert _state(runner)["fo_ban_check"] is False and config.FO_BAN_CHECK_ENABLED is False


def test_a_plan_without_the_runner_pauses_entries_only(tmp_path, atp_live):
    _, me, _, _, _, runner, _ = _runner(tmp_path, "rs4c-plan@example.com")
    _saved_rules(me["tenant_id"], me["id"])
    assert _run(runner.run_cycle(now=NOW)).entries_placed == 1
    assert runner.link.plan_refusal() is None                             # plans carry no `runner` field today
    runner.link.atp_state = {**_state(runner), "plan_runner": False}
    out = _run(runner.run_cycle(now=NOW + timedelta(seconds=5)))           # no heartbeat due: the state stands
    assert out.entries_paused == runner_link.PLAN_NO_RUNNER and "exits continue" in out.entries_paused


# --- 3. LIVE on the runner ----------------------------------------------------------------------------------------------

def test_live_needs_atps_fresh_state_for_his_organisation_his_live_file_and_the_deployment(tmp_path, atp_live):
    _, me, settings, store, _, runner, _ = _runner(tmp_path, "rs4c-live@example.com")
    tenant = me["tenant_id"]
    dep_id = _saved_rules(tenant, me["id"], mode="LIVE")
    first = _run(runner.run_cycle(now=NOW))                               # ATP: LIVE closed (the switch is off)
    assert first.entries_placed == 0 and not _run(store.all(TradeRecord))
    dep = next(d for d in _run(store.all(StrategyDeploymentRecord)) if d.id == dep_id)
    assert dep.last_error == runner_link.STATE_CLOSED.format(reason=live_switch.LIVE_OFF.rstrip(". "))
    assert "nothing was placed" in dep.last_error and "exits continue" in dep.last_error
    assert runtime_mode.live_open() == frozenset() and phase0_lock.locked(tenant)

    atp_live.add(tenant)                                                  # the owner opens LIVE for his organisation
    t2 = NOW + timedelta(seconds=61)
    _run(runner.run_cycle(now=t2))
    assert runner._atp_live_refusal(t2, tenant) is None                   # ATP's side: open, fresh
    assert runtime_mode.live_open() == frozenset({tenant}) and not phase0_lock.locked(tenant)
    assert phase0_lock.locked(tenant + 1) and phase0_lock.locked(None)     # another organisation / unknown: locked

    async def blocks(mode):
        async with store.sessions() as session:
            return await controls.entry_blocks(session, user=await session.get(User, me["id"]), mode=mode, broker_name=None)
    assert phase0_lock.ENTRY_REASON not in _run(blocks("LIVE"))           # the pipeline's own lock check agrees
    assert runner._live_refusal() == LIVE_NEEDS_BROKER                     # his side still decides: a test feed never LIVE
    dep = next(d for d in _run(store.all(StrategyDeploymentRecord)) if d.id == dep_id)
    assert dep.last_error == LIVE_NEEDS_BROKER and not _run(store.all(TradeRecord))

    stale = t2 + timedelta(minutes=settings.link_grace_minutes, seconds=1)   # no answer since: stale past his grace
    assert runner._atp_live_refusal(stale, tenant) == runner_link.STATE_STALE.format(minutes=settings.link_grace_minutes)
    assert runtime_mode.live_open() == frozenset() and phase0_lock.locked(tenant)
    assert phase0_lock.ENTRY_REASON in _run(blocks("LIVE")) and phase0_lock.ENTRY_REASON not in _run(blocks("PAPER"))
    assert runner._atp_live_refusal(t2, tenant + 1).startswith("LIVE is not open for your organisation in ATP")


def test_a_restart_starts_with_live_closed_until_atp_answers(tmp_path):
    _, me, settings, _, _, runner, _ = _runner(tmp_path, "rs4c-restart@example.com")
    assert runner._atp_live_refusal(NOW, me["tenant_id"]) == runner_link.STATE_NEVER
    assert runtime_mode.live_open() == frozenset()


def test_exits_never_read_atps_state(tmp_path, atp_live):
    """A PAPER position's own stop closes it while ATP's LIVE state is closed and stale (ADR-0004)."""
    _, me, settings, store, _, runner, feed = _runner(tmp_path, "rs4c-exit@example.com")
    _saved_rules(me["tenant_id"], me["id"])
    assert _run(runner.run_cycle(now=NOW)).entries_placed == 1
    trade = _run(store.all(TradeRecord))[0]
    runner.link.state_received_at = NOW - timedelta(days=1)                # stale
    feed.set_price("RUNNERX", float(trade.stop_loss) - 1.0)
    out = _run(runner.run_cycle(now=NOW + timedelta(seconds=30)))
    assert out.exits_closed == 1 and [t.exit_reason for t in _run(store.all(TradeRecord))] == ["Stop Loss"]


# --- 4. the flag list -----------------------------------------------------------------------------------------------------

def test_every_flag_the_runner_loop_reads_is_sent_or_listed_with_its_reason(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MIGRATION_DATABASE_URL", "REDIS_URL", "ATP_RUNNER"))}
    env.update(PYTHONPATH=str(BACKEND), FO_BAN_CHECK_ENABLED="true")
    run = subprocess.run([sys.executable, "tests/runner_flag_spy.py", "tests/runner_s4b_fno_job.py", str(tmp_path)], cwd=BACKEND,
                         env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    asked = set(json.loads(next(x for x in run.stdout.splitlines() if x.startswith("FLAGS_ASKED "))[len("FLAGS_ASKED "):]))
    assert "smart_execution" in asked                                     # the spy saw the pipeline's reads
    assert asked <= set(service.RUNNER_FLAGS) | set(NOT_SENT), sorted(asked - set(service.RUNNER_FLAGS) - set(NOT_SENT))
    assert not set(service.RUNNER_FLAGS) & set(NOT_SENT)
    assert all(name in controls.FEATURE_FLAGS for name in service.RUNNER_FLAGS)

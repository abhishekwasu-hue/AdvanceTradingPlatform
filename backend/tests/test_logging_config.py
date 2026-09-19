"""Master prompt Section 49: structured, correlated logging - every log line in the Signal ->
Risk -> Order pipeline should carry tenant_id/strategy_id/order_id so an incident can be traced
end-to-end. Covers app/core/logging_config.py directly (context binding, the filter, the JSON
formatter) and an end-to-end check that a real paper-execute call actually emits log records
carrying those ids.
"""
import json
import logging

from app.core.logging_config import CorrelationFilter, JsonFormatter, bind_log_context, update_log_context
from tests.test_auth_api import _register, client
from tests.test_kill_switch_api import _paper_execute


def test_bind_log_context_sets_and_restores():
    with bind_log_context(tenant_id=1, strategy_id="s1"):
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", (), None)
        CorrelationFilter().filter(record)
        assert record.tenant_id == 1
        assert record.strategy_id == "s1"

    record_after = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", (), None)
    CorrelationFilter().filter(record_after)
    assert not hasattr(record_after, "tenant_id")


def test_bind_log_context_nests_without_clobbering_outer_fields():
    with bind_log_context(tenant_id=1):
        with bind_log_context(strategy_id="inner"):
            record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", (), None)
            CorrelationFilter().filter(record)
            assert record.tenant_id == 1
            assert record.strategy_id == "inner"

        after_inner = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", (), None)
        CorrelationFilter().filter(after_inner)
        assert after_inner.tenant_id == 1
        assert not hasattr(after_inner, "strategy_id")


def test_bind_log_context_drops_none_fields():
    with bind_log_context(tenant_id=1, strategy_id=None):
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", (), None)
        CorrelationFilter().filter(record)
        assert record.tenant_id == 1
        assert not hasattr(record, "strategy_id")


def test_update_log_context_adds_to_currently_active_context_and_is_undone_on_exit():
    with bind_log_context(tenant_id=1):
        update_log_context(order_id=42)
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", (), None)
        CorrelationFilter().filter(record)
        assert record.order_id == 42

    after = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", (), None)
    CorrelationFilter().filter(after)
    assert not hasattr(after, "order_id")


def test_json_formatter_includes_message_and_context_fields():
    with bind_log_context(tenant_id=7, order_id=99):
        record = logging.LogRecord("app.execution", logging.INFO, __file__, 1, "Order filled", (), None)
        formatted = JsonFormatter().format(record)

    payload = json.loads(formatted)
    assert payload["message"] == "Order filled"
    assert payload["level"] == "INFO"
    assert payload["tenant_id"] == 7
    assert payload["order_id"] == 99


def test_paper_execute_pipeline_emits_correlated_log_records(monkeypatch):
    """End-to-end: a real paper-execute call through the API should emit log records from the
    signal-execution/order-persistence modules that carry this request's own tenant_id,
    strategy_id, and order_id - not just that logging.info() was called somewhere.
    """
    token = _register("logging-check@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    records: list[logging.LogRecord] = []

    class _CollectingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _CollectingHandler(level=logging.DEBUG)
    handler.addFilter(CorrelationFilter())
    execution_logger = logging.getLogger("app.execution.signal_execution")
    persistence_logger = logging.getLogger("app.execution.order_persistence")
    previous_level_1, previous_level_2 = execution_logger.level, persistence_logger.level
    execution_logger.addHandler(handler)
    persistence_logger.addHandler(handler)
    execution_logger.setLevel(logging.DEBUG)
    persistence_logger.setLevel(logging.DEBUG)
    try:
        response = _paper_execute(headers, monkeypatch)
    finally:
        execution_logger.removeHandler(handler)
        persistence_logger.removeHandler(handler)
        execution_logger.setLevel(previous_level_1)
        persistence_logger.setLevel(previous_level_2)

    assert response.status_code == 200
    order_id = response.json()["order_id"]
    assert order_id is not None

    assert len(records) > 0
    for record in records:
        assert record.tenant_id is not None
        assert record.strategy_id == "ema_rsi_scalper_1m"
        assert record.order_id == order_id

    messages = [r.getMessage() for r in records]
    assert any("Order created" in m for m in messages)
    assert any("Position opened" in m for m in messages)

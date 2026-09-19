import pytest

from app.core.enums import OrderStatus
from app.execution.order_state_machine import InvalidOrderTransition, assert_valid_transition, is_terminal


def test_happy_path_paper_fill_is_valid():
    hops = [
        OrderStatus.CREATED, OrderStatus.VALIDATING, OrderStatus.RISK_CHECK, OrderStatus.SUBMITTED,
        OrderStatus.PENDING, OrderStatus.FILLED, OrderStatus.POSITION_OPEN,
    ]
    for a, b in zip(hops, hops[1:]):
        assert_valid_transition(a, b)  # must not raise


def test_risk_check_can_reject():
    assert_valid_transition(OrderStatus.RISK_CHECK, OrderStatus.REJECTED)


def test_pending_can_partial_fill_then_fill_or_cancel():
    assert_valid_transition(OrderStatus.PENDING, OrderStatus.PARTIAL_FILL)
    assert_valid_transition(OrderStatus.PARTIAL_FILL, OrderStatus.FILLED)
    assert_valid_transition(OrderStatus.PARTIAL_FILL, OrderStatus.CANCELLED)


@pytest.mark.parametrize(
    "frm,to",
    [
        (OrderStatus.CREATED, OrderStatus.FILLED),
        (OrderStatus.CREATED, OrderStatus.REJECTED),
        (OrderStatus.RISK_CHECK, OrderStatus.FILLED),
        (OrderStatus.RISK_CHECK, OrderStatus.PENDING),
        (OrderStatus.FILLED, OrderStatus.REJECTED),
        (OrderStatus.POSITION_OPEN, OrderStatus.CANCELLED),
        (OrderStatus.REJECTED, OrderStatus.SUBMITTED),
        (OrderStatus.CANCELLED, OrderStatus.FILLED),
    ],
)
def test_illegal_transitions_are_rejected(frm, to):
    with pytest.raises(InvalidOrderTransition):
        assert_valid_transition(frm, to)


def test_terminal_statuses():
    for terminal in (OrderStatus.POSITION_OPEN, OrderStatus.REJECTED, OrderStatus.FAILED, OrderStatus.CANCELLED):
        assert is_terminal(terminal)
    for non_terminal in (OrderStatus.CREATED, OrderStatus.VALIDATING, OrderStatus.RISK_CHECK,
                          OrderStatus.SUBMITTED, OrderStatus.PENDING, OrderStatus.PARTIAL_FILL, OrderStatus.FILLED):
        assert not is_terminal(non_terminal)

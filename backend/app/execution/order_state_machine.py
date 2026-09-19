from app.core.enums import OrderStatus

# The full set of legal transitions (spec order lifecycle): CREATED -> VALIDATING -> RISK_CHECK
# -> SUBMITTED -> PENDING -> FILLED -> POSITION_OPEN on the happy path, with REJECTED/FAILED/
# CANCELLED/PARTIAL_FILL branching off wherever a real broker or the risk engine can produce them.
_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.VALIDATING}),
    OrderStatus.VALIDATING: frozenset({OrderStatus.RISK_CHECK}),
    OrderStatus.RISK_CHECK: frozenset({OrderStatus.SUBMITTED, OrderStatus.REJECTED}),
    OrderStatus.SUBMITTED: frozenset({OrderStatus.PENDING, OrderStatus.REJECTED, OrderStatus.FAILED}),
    OrderStatus.PENDING: frozenset({
        OrderStatus.FILLED, OrderStatus.PARTIAL_FILL, OrderStatus.CANCELLED,
        OrderStatus.REJECTED, OrderStatus.FAILED,
    }),
    OrderStatus.PARTIAL_FILL: frozenset({OrderStatus.FILLED, OrderStatus.CANCELLED}),
    OrderStatus.FILLED: frozenset({OrderStatus.POSITION_OPEN}),
    OrderStatus.POSITION_OPEN: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.FAILED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}

TERMINAL_STATUSES = frozenset({
    OrderStatus.POSITION_OPEN, OrderStatus.REJECTED, OrderStatus.FAILED, OrderStatus.CANCELLED,
})


class InvalidOrderTransition(ValueError):
    """Raised when code attempts a transition the state machine doesn't allow - a bug in the
    calling code, never something a caller's input can trigger."""


def assert_valid_transition(from_status: OrderStatus, to_status: OrderStatus) -> None:
    if to_status not in _TRANSITIONS.get(from_status, frozenset()):
        raise InvalidOrderTransition(f"Cannot transition order from {from_status.value} to {to_status.value}")


def is_terminal(status: OrderStatus) -> bool:
    return status in TERMINAL_STATUSES

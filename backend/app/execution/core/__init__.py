"""N1 E1: the execution core (ADR-0014, design note docs/design/N1_EXECUTION_CORE.md).

One way for an automatic entry to become something a person sees: a producer writes an *intent* (`intents.submit`),
the trading worker claims it (`dispatcher.drain`) and one pure policy (`policy.decide`) routes it:

* NOTIFY  - a notification to the intent's owner, nothing else;
* PROPOSE - a Z3 ENTRY proposal (`approval.propose_entry`): Approve places a PAPER order through the existing approval
  path, never a broker order;
* REFUSE  - nothing, with the reason on the row (Auto LIVE always; bad levels; owner gone; paused / locked out).

PAPER only. Nothing in this package holds, builds or is given a broker, and nothing calls an order pipeline: the
`PAPER` route exists in the policy for U4 X2 (Auto PAPER) and is refused by the dispatcher until then. A test walks
the Z10-c call graph and fails if any function here can reach a broker order method. Flag `execution_core`, off.
"""

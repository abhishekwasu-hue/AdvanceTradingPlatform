"""Runner S3 (docs/design/USER_RUNNER.md section 4; flag `user_runner`, OFF): the ATP side of the link to the user's own
runner - device pairing, the signed heartbeat, the read-only mirror uploads and the "runner offline" alert.

What ATP does NOT do here (owner rules): it never evaluates a runner user's strategy, never places or forwards an order,
never holds a broker key / token, and never flips a runner's kill switch - it stores what the device reports; the user's
own commands reach his runner in S4. There is no admin or platform path to stop a user's runner.
"""

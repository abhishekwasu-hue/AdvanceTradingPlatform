"""Route 1 runner (docs/design/USER_RUNNER.md): ATP's order path on the user's own machine.

Step S1 holds only the module boundary (`manifest.py`): which backend modules the runner may import, and which it must
never reach (the private package and server-only features). tests/test_runner_s1_boundary.py enforces it. Nothing here
is imported by the server; nothing here places an order.
"""

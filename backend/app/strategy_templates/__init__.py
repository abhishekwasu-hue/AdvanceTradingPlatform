"""Strategy templates (owner order ATP_SESSION_STRATEGY_TEMPLATES.md): versioned white-box SIGNAL recipes.

schema   - the template format (pydantic, strict)
compile  - binding to the ScreenQL registry (refuses LAST_BAR_ONLY / unknown names), the user's settings, the params hash
engine   - the pure, causal setup / trigger state machine over a whole series (S1-S7, R3, R8)
adapter  - a template as a BaseStrategy that reads that series (not wired to deployments yet)
language - the signal text tokens and their guard (policy 11l)
library  - the built-in templates (templates/<id>.json)

Templates only generate signals. Nothing here places, sizes or exits an order.
"""

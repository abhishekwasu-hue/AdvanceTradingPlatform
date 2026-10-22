"""Analyze a chart (docs/design/CHART_ANALYZE.md): a user uploads a chart image or pastes a chart link and gets an
educational reading of it - the situation and the possibilities, never advice. Behind the `chart_analyze` flag (off).

Modules: `settings` (admin settings with their defaults), `imaging` (safe decode and re-encode), `linkfetch` (the
SSRF-safe link fetch), `storage` (the private per-tenant file store), `provider` (the boundary to the private package),
`contract` (the output contract and its validation), `limits` (plan limits and the AI cost cap), `engine` (the AI call
on ATP's existing AI path), `service` (items, history, delete, retention) and `routes` (the API).

Nothing here places, changes or proposes an order.
"""
FLAG = "chart_analyze"
# The "Option strikes · from your settings" block (PR-A3): its own flag, on top of FLAG (owner's tenant first).
STRIKES_FLAG = "chart_analyze_strikes"

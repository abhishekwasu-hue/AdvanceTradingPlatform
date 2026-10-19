"""Regenerate docs/API.md from the running application's OpenAPI schema."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.api.main import create_app  # noqa: E402
from src.services.app_services import Services  # noqa: E402

spec = create_app(Services(db_url="sqlite:///:memory:")).openapi()
L = ["# REST API (`/api/v1`)", "", "Generated from the application's OpenAPI schema by `python scripts/gen_api_docs.py`. "
     "Interactive docs: `http://127.0.0.1:8000/docs`.", "",
     "**Auth:** `Authorization: Bearer <token>` (create with `python -m src.api.manage create-user <name> [admin|analyst|viewer]`). "
     "`AMW_AUTH_MODE=local` disables auth for single-user desktop use. **Roles:** viewer = read-only, analyst = scans/own data, "
     "admin = everything. **Errors:** JSON `{\"detail\": ...}` (4xx) or `{\"error\": ..., \"request_id\": ...}` (429/500); every "
     "response carries `X-Request-ID`. **Rate limit:** `AMW_RATE_LIMIT_PER_MIN` per token/IP. There are **no order-placement endpoints**; "
     "the `/broker/fyers` routes only connect the user's own FYERS account for market data.",
     "", "**AI (BYOK):** `/api/v1/ai/*` endpoints use only the caller's own provider keys; keys are accepted on `PUT /ai/keys/{provider}` "
     "and never returned (masked only). See docs/AI_BYOK.md.", "",
     "| Method | Path | Summary | Parameters / body |", "|---|---|---|---|"]
for path, ops in spec["paths"].items():
    for m, op in ops.items():
        ps = ", ".join(f"`{p['name']}`" for p in op.get("parameters", []) if p["name"] != "authorization")
        body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {}).get("$ref", "")
        if body:
            ps = (ps + ", " if ps else "") + "body `" + body.split("/")[-1] + "`"
        L.append(f"| {m.upper()} | `{path}` | {op.get('summary', '')} | {ps or '-'} |")
L += ["", "## Request schemas", ""]
for name, sch in spec["components"]["schemas"].items():
    if name.endswith("In") or name == "ScanRequest":
        req = set(sch.get("required", []))
        L.append(f"* **{name}** — " + ", ".join(f"`{k}`{'*' if k in req else ''}" for k in sch.get("properties", {})))
L.append("\n`*` = required.")
L += ["", "## WebSocket", "",
      "`WS /api/v1/stream/bars` — live stream of the caller's **own** completed 1-minute FYERS bars (not in the OpenAPI "
      "schema). First message within 10 s: `{\"token\": \"<api token>\", \"symbols\": [\"RELIANCE.NS\"]}` (token omitted in "
      "`AMW_AUTH_MODE=local`). Messages: `subscribed`, `bar`, `revision` (upsert by `symbol`+`ts`), `status`, `error` "
      "(close code 4401). Details: docs/LIVE_DATA_FYERS.md."]
open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "API.md"), "w").write("\n".join(L) + "\n")
print(sum(1 for x in L if x.startswith("| ") and "`/" in x), "endpoints")

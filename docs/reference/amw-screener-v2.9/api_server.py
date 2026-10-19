"""Run the REST API:  python api_server.py   (docs at http://127.0.0.1:8000/docs)"""
import logging
import os

import uvicorn

os.environ.setdefault("AMW_SCHEDULER", "1")      # run scheduled scans/reports in the API process
os.environ.setdefault("AMW_FEED_AUTORESUME", "1")  # restart FYERS feeds users left running (one per user, DB lease)

from src.api.main import create_app

logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
app = create_app()


@app.on_event("startup")
async def _thread_limit():
    """Worker threads for synchronous endpoints (default 40 = framework default). Measured (docs/autonomous/
    PERFORMANCE_RESULTS.md, P-002): with 20 concurrent clients, fewer threads reduce GIL contention (signal list:
    15 req/s at 40 threads, 27 at 4, 72 at 1) but one slow request (backtest, report, AI call) then blocks more
    of the others. Change only after measuring your own workload."""
    n = os.environ.get("AMW_API_THREADS")
    if n:
        import anyio
        anyio.to_thread.current_default_thread_limiter().total_tokens = max(1, int(n))

if __name__ == "__main__":
    workers = int(os.environ.get("AMW_API_WORKERS", "1"))
    host, port = os.environ.get("AMW_API_HOST", "127.0.0.1"), int(os.environ.get("AMW_API_PORT", "8000"))
    if workers > 1:
        # Multi-process API (measured: signal list 14 -> 197 req/s with 4 workers at 20 concurrent clients). Safe because
        # scheduled slots are claimed atomically, the FYERS feed uses a DB lease, jobs and results are DB-recorded and the
        # WebSocket stream tails the database. The per-key rate limit is per process (effective limit x workers).
        uvicorn.run("api_server:app", host=host, port=port, workers=workers)
    else:
        uvicorn.run(app, host=host, port=port)

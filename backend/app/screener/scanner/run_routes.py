"""`POST /api/scanner/run`: the Market Scanner's run over the symbols and candles the request sends.

M55 Q1: moved from app/main.py (which now only includes routers) as it was - only the annotations are in built-in
generics; included at the end of the router list in the same order as before, so every path, method, dependency
and the match order are unchanged (tests/test_m55_q1_route_table.py).
"""
from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from app.auth.dependencies import get_current_user
from app.core.rate_limit import analysis_rate_limit
from app.db.models import User
from app.screener.scanner.engine import run_scanner
from app.screener.scanner.models import ScannerRequest, ScannerResult

router = APIRouter()


@router.post("/api/scanner/run", response_model=ScannerResult, dependencies=[Depends(analysis_rate_limit)])
async def scanner_run(request: ScannerRequest, _: User = Depends(get_current_user)) -> ScannerResult:
    """Runs configurable indicator/price-action-structure/option-chain filters across a supplied
    list of symbols (each with its own OHLCV candles and, optionally, option chain) and returns
    only the ones that clear every filter. Indicator filters reuse the exact same `Condition`
    building block the no-code Strategy Builder uses. Pure function of its input - no persistence,
    needs a logged-in caller (P0.1 / S3: CPU-bound work is not offered to anonymous callers).
    """
    from app.core import config as app_config
    if app_config.SCANNER_ENGINE == "screenql":
        from app.screener.scanner.screenql import run_scanner_screenql
        return await run_in_threadpool(run_scanner_screenql, request)
    return await run_in_threadpool(run_scanner, request)

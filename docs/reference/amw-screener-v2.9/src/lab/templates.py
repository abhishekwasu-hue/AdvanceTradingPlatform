"""Starter templates for code languages. Every template is executed in tests on synthetic fixtures."""

PY_HEADER = "# ctx: meta, series (index -1 = last completed bar, None = missing), structure, htf, params. Helpers: amw.*\n"

PYTHON = {
    "Trend (EMA stack)": PY_HEADER + '''def evaluate(ctx):
    s = ctx["series"]
    c, e20, e50, e200, a = (amw.last(s[k]) for k in ("close", "ema20", "ema50", "ema200", "atr14"))
    rules = [
        {"name": "close > EMA20", "passed": None if None in (c, e20) else c > e20},
        {"name": "EMA20 > EMA50", "passed": None if None in (e20, e50) else e20 > e50},
        {"name": "EMA50 > EMA200", "passed": None if None in (e50, e200) else e50 > e200},
    ]
    ok = all(r["passed"] is True for r in rules)
    out = {"status": "qualified" if ok else "rejected", "bias": "long" if ok else "neutral",
           "classification": "trend", "rules": rules}
    if ok and a is not None:
        out.update(stop=c - 2 * a, target=c + 4 * a, invalidation=c - 2 * a, entry="next bar")
    return out
''',
    "Breakout (20-bar high + volume)": PY_HEADER + '''def evaluate(ctx):
    s = ctx["series"]
    n = int(ctx["params"].get("lookback", 20))
    c, a, rv = amw.last(s["close"]), amw.last(s["atr14"]), amw.last(s["rvol"])
    prior_high = amw.highest(s["high"][:-1], n)          # excludes the current bar -> no self-reference
    brk = None if None in (c, prior_high) else c > prior_high
    vol = None if rv is None else rv >= 1.5
    rules = [{"name": f"close > prior {n}-bar high", "passed": brk, "detail": str(prior_high)},
             {"name": "RVOL >= 1.5", "passed": vol, "detail": str(rv)}]
    ok = brk is True and vol is True
    out = {"status": "qualified" if ok else "rejected", "bias": "long" if ok else "neutral",
           "classification": "breakout", "rules": rules}
    if ok and a is not None:
        out.update(stop=prior_high - 0.5 * a, target=c + 2 * (c - (prior_high - 0.5 * a)))
    return out
''',
    "Pullback (EMA20 in uptrend)": PY_HEADER + '''def evaluate(ctx):
    s = ctx["series"]
    c, e20, e50, a = (amw.last(s[k]) for k in ("close", "ema20", "ema50", "atr14"))
    lows = [v for v in s["low"][-3:] if v is not None]
    if None in (c, e20, e50, a) or not lows:
        return {"status": "rejected", "bias": "neutral", "rules": [{"name": "data", "passed": None}]}
    trend = e20 > e50
    touch = min(lows) <= e20 + 0.5 * a
    hold = c > e20
    rules = [{"name": "EMA20 > EMA50", "passed": trend}, {"name": "touched EMA20", "passed": touch},
             {"name": "closed above EMA20", "passed": hold}]
    ok = trend and touch and hold
    out = {"status": "qualified" if ok else "rejected", "bias": "long" if ok else "neutral", "classification": "pullback", "rules": rules}
    if ok:
        stop = min(lows) - 0.2 * a
        out.update(stop=stop, target=c + 2 * (c - stop))
    return out
''',
    "Reversal (oversold + bullish CHoCH)": PY_HEADER + '''def evaluate(ctx):
    s, st = ctx["series"], ctx["structure"]
    n = len(s["close"])
    r = [v for v in s["rsi14"][-10:] if v is not None]
    choch = any(e["type"] == "CHoCH" and e["direction"] == "bullish" and e["index"] >= n - 10 for e in st["events"])
    oversold = bool(r) and min(r) < 30
    c, a = amw.last(s["close"]), amw.last(s["atr14"])
    rules = [{"name": "RSI < 30 in last 10 bars", "passed": oversold if r else None},
             {"name": "bullish CHoCH in last 10 bars", "passed": choch}]
    ok = oversold and choch and a is not None
    out = {"status": "qualified" if ok else "rejected", "bias": "long" if ok else "neutral", "classification": "reversal", "rules": rules}
    if ok:
        low = min(v for v in s["low"][-10:] if v is not None)
        out.update(stop=low - 0.2 * a, target=c + 2 * (c - (low - 0.2 * a)))
    return out
''',
    "Relative volume spike": PY_HEADER + '''def evaluate(ctx):
    s = ctx["series"]
    rv, c, o = amw.last(s["rvol"]), amw.last(s["close"]), amw.last(s["open"])
    thr = float(ctx["params"].get("min_rvol", 2.0))
    spike = None if rv is None else rv >= thr
    rules = [{"name": f"RVOL >= {thr}", "passed": spike, "detail": str(rv)}]
    if spike is not True:
        return {"status": "rejected", "bias": "neutral", "classification": "volume", "rules": rules}
    bias = "long" if c > o else "short"
    return {"status": "qualified", "bias": bias, "classification": "volume", "rules": rules,
            "explanation": "volume spike; direction from candle body"}
''',
    "Multi-timeframe confluence": PY_HEADER + '''def evaluate(ctx):
    w = (ctx.get("htf") or {}).get("1W")
    st = ctx["structure"]
    rules = [{"name": "weekly trend Bullish", "passed": None if not w else w["trend_label"] == "Bullish"},
             {"name": "daily trend Bullish", "passed": None if st["trend_label"] == "NA" else st["trend_label"] == "Bullish"},
             {"name": "daily structure up", "passed": None if st["structure_trend"] is None else st["structure_trend"] == "up"}]
    ok = all(r["passed"] is True for r in rules)
    s = ctx["series"]
    c, a = amw.last(s["close"]), amw.last(s["atr14"])
    out = {"status": "qualified" if ok else "rejected", "bias": "long" if ok else "neutral", "classification": "mtf", "rules": rules}
    if ok and a is not None:
        out.update(stop=c - 2 * a, target=c + 4 * a)
    return out
''',
}

JAVASCRIPT = {
    "Trend (EMA stack)": '''// ctx.series arrays: index length-1 = last completed bar; null = missing. Helpers: amw.*
function evaluate(ctx) {
  const s = ctx.series;
  const [c, e20, e50, e200, a] = ['close', 'ema20', 'ema50', 'ema200', 'atr14'].map((k) => amw.last(s[k]));
  const cmp = (x, y) => (x === null || y === null ? null : x > y);
  const rules = [{ name: 'close > EMA20', passed: cmp(c, e20) }, { name: 'EMA20 > EMA50', passed: cmp(e20, e50) },
                 { name: 'EMA50 > EMA200', passed: cmp(e50, e200) }];
  const ok = rules.every((r) => r.passed === true);
  const out = { status: ok ? 'qualified' : 'rejected', bias: ok ? 'long' : 'neutral', classification: 'trend', rules };
  if (ok && a !== null) { out.stop = c - 2 * a; out.target = c + 4 * a; out.invalidation = out.stop; }
  return out;
}
''',
    "Relative volume spike": '''function evaluate(ctx) {
  const s = ctx.series;
  const rv = amw.last(s.rvol), c = amw.last(s.close), o = amw.last(s.open);
  const spike = rv === null ? null : rv >= 2;
  const rules = [{ name: 'RVOL >= 2', passed: spike, detail: String(rv) }];
  if (spike !== true) return { status: 'rejected', bias: 'neutral', classification: 'volume', rules };
  return { status: 'qualified', bias: c > o ? 'long' : 'short', classification: 'volume', rules };
}
''',
}

TYPESCRIPT = {
    "Trend (EMA stack)": '''// Type-checked against the AMW SDK (Context, Result, amw). Index length-1 = last completed bar.
function evaluate(ctx: Context): Result {
  const s = ctx.series;
  const c = amw.last(s.close), e20 = amw.last(s.ema20), e50 = amw.last(s.ema50), e200 = amw.last(s.ema200), a = amw.last(s.atr14);
  const cmp = (x: number | null, y: number | null): boolean | null => (x === null || y === null ? null : x > y);
  const rules = [{ name: 'close > EMA20', passed: cmp(c, e20) }, { name: 'EMA20 > EMA50', passed: cmp(e20, e50) },
                 { name: 'EMA50 > EMA200', passed: cmp(e50, e200) }];
  const ok = rules.every((r) => r.passed === true);
  const out: Result = { status: ok ? 'qualified' : 'rejected', bias: ok ? 'long' : 'neutral', classification: 'trend', rules };
  if (ok && c !== null && a !== null) { out.stop = c - 2 * a; out.target = c + 4 * a; out.invalidation = out.stop; }
  return out;
}
''',
}

LUA = {
    "Trend (EMA stack)": '''-- Arrays are 1-based; amw.last(s) = last completed bar; missing = NaN (use amw.isna)
function evaluate(ctx)
  local s = ctx.series
  local c, e20, e50, e200, a = amw.last(s.close), amw.last(s.ema20), amw.last(s.ema50), amw.last(s.ema200), amw.last(s.atr14)
  local function cmp(x, y) if amw.isna(x) or amw.isna(y) then return nil end return x > y end
  local r1, r2, r3 = cmp(c, e20), cmp(e20, e50), cmp(e50, e200)
  local ok = (r1 == true) and (r2 == true) and (r3 == true)
  local out = {status = ok and 'qualified' or 'rejected', bias = ok and 'long' or 'neutral', classification = 'trend',
               rules = {{name = 'close > EMA20', passed = r1}, {name = 'EMA20 > EMA50', passed = r2}, {name = 'EMA50 > EMA200', passed = r3}}}
  if ok and not amw.isna(a) then out.stop = c - 2 * a; out.target = c + 4 * a; out.invalidation = out.stop end
  return out
end
''',
    "Relative volume spike": '''function evaluate(ctx)
  local s = ctx.series
  local rv = amw.last(s.rvol)
  local spike = nil
  if not amw.isna(rv) then spike = rv >= 2 end
  if spike ~= true then return {status = 'rejected', bias = 'neutral', classification = 'volume', rules = {{name = 'RVOL >= 2', passed = spike}}} end
  local bias = 'short'
  if amw.last(s.close) > amw.last(s.open) then bias = 'long' end
  return {status = 'qualified', bias = bias, classification = 'volume', rules = {{name = 'RVOL >= 2', passed = true}}}
end
''',
}

R = {
    "Trend (EMA stack)": '''# Base R + stats. ctx$series vectors; amw$last(x) = last completed bar; missing = NA.
evaluate <- function(ctx) {
  s <- ctx$series
  c <- amw$last(s$close); e20 <- amw$last(s$ema20); e50 <- amw$last(s$ema50); e200 <- amw$last(s$ema200); a <- amw$last(s$atr14)
  cmp <- function(x, y) if (is.na(x) || is.na(y)) NA else x > y
  r <- list(cmp(c, e20), cmp(e20, e50), cmp(e50, e200))
  ok <- all(vapply(r, function(v) isTRUE(v), TRUE))
  out <- list(status = if (ok) "qualified" else "rejected", bias = if (ok) "long" else "neutral", classification = "trend",
              rules = list(list(name = "close > EMA20", passed = r[[1]]), list(name = "EMA20 > EMA50", passed = r[[2]]),
                           list(name = "EMA50 > EMA200", passed = r[[3]])))
  if (ok && !is.na(a)) { out$stop <- c - 2 * a; out$target <- c + 4 * a; out$invalidation <- out$stop }
  out
}
''',
    "Statistical: return z-score": '''# Flags an unusual 1-bar return: z = (r - mean) / sd over the previous 60 returns.
evaluate <- function(ctx) {
  cl <- ctx$series$close
  r <- diff(log(cl))
  if (length(r) < 61 || anyNA(tail(r, 61))) return(list(status = "rejected", bias = "neutral", rules = list(list(name = "60 returns", passed = NA))))
  hist <- head(tail(r, 61), 60); z <- (tail(r, 1) - mean(hist)) / sd(hist)
  big <- abs(z) >= 2
  list(status = if (big) "qualified" else "rejected", bias = if (!big) "neutral" else if (z > 0) "long" else "short",
       classification = "statistical", rules = list(list(name = "|z| >= 2", passed = big, detail = format(round(z, 3)))),
       explanation = paste("return z-score", round(z, 2)))
}
''',
}

CODE_TEMPLATES = {"python": PYTHON, "javascript": JAVASCRIPT, "typescript": TYPESCRIPT, "lua": LUA, "r": R}

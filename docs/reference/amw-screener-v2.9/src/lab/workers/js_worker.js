// AMW Strategy Lab worker for JavaScript / TypeScript (TypeScript is type-checked + transpiled by ts_compile.js first).
// Runs under: unshare -n -m + setpriv(nobody) + rlimits + `node --permission` (no fs/child_process/worker/wasi).
// Realm isolation: the vm context is created from a NULL-PROTOTYPE object; every object the strategy can reach
// (console, ctx, amw helpers) is created INSIDE the context realm, and results leave the context only as JSON strings,
// so no host-realm object (and therefore no host `Function` constructor) is reachable. Code generation from strings and
// WebAssembly are disabled in the context.
'use strict';
const vm = require('vm');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => { input += c; });
process.stdin.on('end', () => {
  const req = JSON.parse(input);
  const out = { runtime_version: 'Node ' + process.version, check: { ok: true, errors: [] }, results: [] };
  const PRELUDE = `
    'use strict';
    var __logs = [];
    var console = Object.freeze({ log: (...a) => { if (__logs.length < 200) __logs.push(a.map(String).join(' ').slice(0, 500)); } });
    var amw = Object.freeze({
      isna: (x) => x === null || x === undefined || Number.isNaN(x),
      last: (s, n = 0) => (s.length > n ? s[s.length - 1 - n] : null),
      prev: (s, n = 1) => (s.length > n ? s[s.length - 1 - n] : null),
      highest: (s, n) => { const w = s.slice(-n); return (w.length === n && w.every((v) => v !== null)) ? Math.max(...w) : null; },
      lowest: (s, n) => { const w = s.slice(-n); return (w.length === n && w.every((v) => v !== null)) ? Math.min(...w) : null; },
      crossover: (a, b) => { const v = [a.at(-1), a.at(-2), b.at(-1), b.at(-2)];
        return v.some((x) => x === null || x === undefined) ? null : (v[0] > v[2] && v[1] <= v[3]); },
      crossunder: (a, b) => { const v = [a.at(-1), a.at(-2), b.at(-1), b.at(-2)];
        return v.some((x) => x === null || x === undefined) ? null : (v[0] < v[2] && v[1] >= v[3]); },
    });
    Math.random = () => { throw new Error('Math.random is disabled (non-deterministic)'); };
    var Date = undefined;
    var __deepFreeze = (o) => { if (o && typeof o === 'object') { Object.values(o).forEach(__deepFreeze); Object.freeze(o); } return o; };
  `;
  let script;
  try {
    script = new vm.Script(req.source + '\n;', { filename: 'strategy.js' });
  } catch (e) {
    out.check = { ok: false, errors: [String(e.message).slice(0, 300)] };
    process.stdout.write('@@AMW@@' + JSON.stringify(out) + '\n');
    return;
  }
  const ctxs = req.mode === 'check' ? [null] : req.contexts;
  const tmo = req.timeout_ms || 2000;
  for (const c of ctxs) {
    const sandbox = vm.createContext(Object.create(null), { codeGeneration: { strings: false, wasm: false } });
    let logs = '[]';
    try {
      new vm.Script(PRELUDE).runInContext(sandbox, { timeout: 1000 });
      script.runInContext(sandbox, { timeout: tmo });
      const isFn = new vm.Script('typeof evaluate === "function"').runInContext(sandbox);
      if (isFn !== true) { out.check = { ok: false, errors: ['define a function evaluate(ctx)'] }; break; }
      if (req.mode === 'check') break;
      // data enters as a string literal and is parsed INSIDE the context realm
      new vm.Script('var __ctx = __deepFreeze(JSON.parse(' + JSON.stringify(JSON.stringify(c)) + '));').runInContext(sandbox);
      const s = new vm.Script('(() => { const r = evaluate(__ctx); return JSON.stringify(r === undefined ? null : r); })()')
        .runInContext(sandbox, { timeout: tmo });
      logs = new vm.Script('JSON.stringify(__logs)').runInContext(sandbox);
      if (typeof s !== 'string') throw new Error('result is not serialisable');
      out.results.push({ raw: JSON.parse(s), error: null, logs: JSON.parse(logs).join('\n').slice(-4000) });
    } catch (e) {
      let msg = 'error';
      try { msg = String(e && e.message !== undefined ? e.message : e); } catch (_) { /* ignore */ }
      try { logs = new vm.Script('JSON.stringify(__logs)').runInContext(sandbox); } catch (_) { /* ignore */ }
      out.results.push({ raw: null, error: msg.slice(0, 300), logs: JSON.parse(typeof logs === 'string' ? logs : '[]').join('\n').slice(-4000) });
    }
  }
  process.stdout.write('@@AMW@@' + JSON.stringify(out) + '\n');
});

// TypeScript compile step (trusted compiler; user code is only type-checked and transpiled, never executed here).
// Runs in the same process sandbox with `node --permission --allow-fs-read=<typescript package dir>`.
'use strict';
const TS_PATH = process.env.AMW_TS_PATH;
const ts = require(TS_PATH);
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => { input += c; });
process.stdin.on('end', () => {
  const { source, sdk } = JSON.parse(input);
  const files = { 'strategy.ts': source, 'amw-sdk.d.ts': sdk };
  const options = { target: ts.ScriptTarget.ES2020, strict: true, noEmit: true, lib: ['lib.es2020.d.ts'], types: [] };
  const host = ts.createCompilerHost(options);
  const orig = host.getSourceFile;
  host.getSourceFile = (name, lang) => (files[name] !== undefined
    ? ts.createSourceFile(name, files[name], lang) : orig.call(host, name, lang));
  host.fileExists = (n) => files[n] !== undefined || ts.sys.fileExists(n);
  host.readFile = (n) => (files[n] !== undefined ? files[n] : ts.sys.readFile(n));
  const program = ts.createProgram(['strategy.ts', 'amw-sdk.d.ts'], options, host);
  const diags = ts.getPreEmitDiagnostics(program).filter((d) => !d.file || d.file.fileName === 'strategy.ts');
  const errors = diags.map((d) => {
    const msg = ts.flattenDiagnosticMessageText(d.messageText, '\n');
    if (d.file && d.start !== undefined) { const p = d.file.getLineAndCharacterOfPosition(d.start); return `line ${p.line + 1}:${p.character + 1} TS${d.code}: ${msg}`; }
    return `TS${d.code}: ${msg}`;
  });
  const js = errors.length ? null : ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None } }).outputText;
  process.stdout.write('@@AMW@@' + JSON.stringify({ ok: errors.length === 0, errors, js, version: ts.version }) + '\n');
});

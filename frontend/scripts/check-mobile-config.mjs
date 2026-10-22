#!/usr/bin/env node
// Mobile B1 (docs/design/MOBILE_APP_PLAN.md §4.2, ADR-0028 decision 10): the app's configuration lint. Runs in the
// frontend CI job (next to the colour lint) and before every Android build (.github/workflows/android.yml). Fails on:
//  - an "/api/..." string literal in src/ outside src/api/base.ts (the app's API is on another origin; every URL must
//    come from api/base.ts) - tests and comments excepted;
//  - an IP address in src/, capacitor.config.ts or android/ (a public address anywhere at all, tests included; the
//    documentation ranges 192.0.2/198.51.100/203.0.113 and loopback are allowed outside tests, and private ranges in tests);
//  - ATP_ANDROID_BUILD / VITE_API_BASE_URL that break the build rules (scripts/mobile-rules.mjs): an IP or non-https
//    API base, a real server in a debuggable build, a release with the temporary package id;
//  - ATP_APP_DOMAIN (the GitHub variable, MA-1), when set, that is not a plain host name or is an IP address;
//  - the package id in android/app/build.gradle (namespace, applicationId), res/values/strings.xml or the MainActivity
//    package differing from capacitor.config.ts (scripts/mobile-rules.mjs APP_ID);
//  - a keystore, google-services.json or local.properties inside android/ (secrets never live in git);
//  - with --bundle: dist/ not built by `npm run build:android` for the same build kind and API origin.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { isMain, stripComments } from "./check-v2-colours.mjs";
import { APP_ID, NATIVE_BUILD_MARKER, apiBaseFromDomain, apiBaseProblems, resolveNativeBuild } from "./mobile-rules.mjs";

/** The only file in src/ that may spell an "/api/" URL. */
export const API_BASE_FILE = "src/api/base.ts";

const API_LITERAL = /(["'`])\/api\/[^"'`\n]*/g;

/** Every quoted "/api/..." literal in `text` (comments ignored), as { line, match }. */
export function findApiLiterals(text) {
  const out = [];
  stripComments(text).split("\n").forEach((line, i) => {
    API_LITERAL.lastIndex = 0;
    for (const m of line.matchAll(API_LITERAL)) out.push({ line: i + 1, match: m[0] });
  });
  return out;
}

const OCTET = "(?:25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)";
const IPV4 = new RegExp(`(?<![\\d.])${OCTET}(?:\\.${OCTET}){3}(?![\\d]|\\.\\d)`, "g");

/** RFC 5737 documentation ranges, loopback and the unspecified address: never a real host. */
export function isAllowedIp(ip) {
  return /^(192\.0\.2|198\.51\.100|203\.0\.113)\.\d+$/.test(ip) || /^127\./.test(ip) || ip === "0.0.0.0";
}

/** True for a public (globally routable) IPv4 address: not documentation, loopback, unspecified, private (10/8,
 * 172.16/12, 192.168/16), link-local, shared (100.64/10), benchmarking (198.18/15), multicast or reserved. */
export function isPublicIp(ip) {
  const [a, b] = ip.split(".").map(Number);
  if (isAllowedIp(ip) || a === 0 || a === 10 || a === 127 || a >= 224) return false;
  if ((a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254)) return false;
  if ((a === 100 && b >= 64 && b <= 127) || (a === 198 && (b === 18 || b === 19))) return false;
  return true;
}

/** Every IPv4 literal in `text` that is not allowed, as { line, match }. With `strictOnly`, only a public address
 * counts - used for tests, which may name private or documentation example addresses. A real server's address is
 * never written in this public repository (docs/PUBLIC_REPO_POLICY.md). */
export function findIpLiterals(text, { strictOnly = false } = {}) {
  const out = [];
  text.split("\n").forEach((line, i) => {
    IPV4.lastIndex = 0;
    for (const m of line.matchAll(IPV4)) {
      const ip = m[0];
      if (strictOnly ? isPublicIp(ip) : !isAllowedIp(ip)) out.push({ line: i + 1, match: ip });
    }
  });
  return out;
}

/** The `applicationId` an app/build.gradle declares, or null. */
export function gradleApplicationId(gradleText) {
  const m = /^\s*applicationId\s*[= ]\s*["']([^"']+)["']/m.exec(gradleText);
  return m ? m[1] : null;
}

/** The `namespace` an app/build.gradle declares, or null. */
export function gradleNamespace(gradleText) {
  const m = /^\s*namespace\s*=?\s*["']([^"']+)["']/m.exec(gradleText);
  return m ? m[1] : null;
}

/** Problems with the Android project's package id (empty list = fine). */
export function appIdProblems(gradleText, appId = APP_ID) {
  const found = gradleApplicationId(gradleText);
  if (found === null) return ["android/app/build.gradle declares no applicationId."];
  const problems = [];
  if (found !== appId) {
    problems.push(`android/app/build.gradle applicationId "${found}" differs from capacitor.config.ts appId "${appId}" (scripts/mobile-rules.mjs APP_ID).`);
  }
  const namespace = gradleNamespace(gradleText);
  if (namespace !== null && namespace !== appId) {
    problems.push(`android/app/build.gradle namespace "${namespace}" differs from the package id "${appId}".`);
  }
  return problems;
}

/** Problems with res/values/strings.xml: `package_name` and `custom_url_scheme` are the package id. */
export function stringsProblems(xml, appId = APP_ID) {
  const problems = [];
  for (const name of ["package_name", "custom_url_scheme"]) {
    const m = new RegExp(`<string name="${name}">([^<]*)</string>`).exec(xml);
    if (!m) problems.push(`android/app/src/main/res/values/strings.xml has no ${name}.`);
    else if (m[1].trim() !== appId) problems.push(`strings.xml ${name} "${m[1].trim()}" differs from the package id "${appId}".`);
  }
  return problems;
}

/** Problems with the MainActivity source files (paths relative to android/app/src/main/java, with their text): exactly
 * one, in the package id's directory, declaring that package. */
export function activityProblems(files, appId = APP_ID) {
  const want = `${appId.split(".").join("/")}/MainActivity.java`;
  const found = files.filter((f) => /(^|\/)MainActivity\.(java|kt)$/.test(f.path));
  if (found.length !== 1 || found[0].path !== want) {
    return [`MainActivity must be exactly android/app/src/main/java/${want}; found: ${found.map((f) => f.path).join(", ") || "none"}.`];
  }
  const pkg = /^\s*package\s+([\w.]+)\s*;?/m.exec(found[0].text);
  return pkg && pkg[1] === appId ? [] : [`${want} declares package "${pkg ? pkg[1] : "(none)"}", not "${appId}".`];
}

/** True for a file that must never be in the Android project (secrets and machine-local settings). */
export function isForbiddenAndroidFile(path) {
  const name = path.split(/[\\/]/).pop() ?? "";
  return /\.(jks|keystore|p12)$/i.test(name) || name === "google-services.json" || name === "local.properties";
}

/** Problems with a built bundle's marker against this build's settings (empty list = fine). */
export function bundleProblems(marker, build) {
  if (!marker) return [`dist/${NATIVE_BUILD_MARKER} is missing: dist/ is not the app's bundle. Run \`npm run build:android\`.`];
  if (marker.kind !== build.kind || marker.apiOrigin !== build.apiBase || marker.appId !== build.appId) {
    return [`dist/ was built for ${marker.kind} / ${marker.apiOrigin} / ${marker.appId}, not ${build.kind} / ${build.apiBase} / ${build.appId}.`];
  }
  return [];
}

/** Directories under android/ that are generated or copied (not source): skipped. */
const ANDROID_SKIP = new Set([".gradle", "build", ".idea", "capacitor-cordova-android-plugins"]);
const BINARY = /\.(png|jpe?g|webp|gif|jar|apk|aab|so|keystore|jks|p12)$/i;

function walk(dir, skip = new Set(), files = []) {
  if (!existsSync(dir)) return files;
  for (const entry of readdirSync(dir)) {
    if (skip.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, skip, files);
    else files.push(full);
  }
  return files;
}

/** Files under `dir` that git would commit (tracked, or new and not ignored), relative to `root`. Outside a git
 * checkout: every file under it. */
function committable(root, dir) {
  try {
    const out = execFileSync("git", ["ls-files", "--cached", "--others", "--exclude-standard", "--", dir], { cwd: root, encoding: "utf8" });
    return out.split("\n").filter(Boolean);
  } catch {
    return walk(join(root, dir)).map((f) => relative(root, f).split(sep).join("/"));
  }
}

function main() {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..");
  const rel = (f) => relative(root, f).split(sep).join("/");
  const problems = [];

  // 1. API URLs only through api/base.ts.
  const srcFiles = walk(join(root, "src")).filter((f) => /\.(ts|tsx)$/.test(f));
  for (const f of srcFiles) {
    const r = rel(f);
    if (r === API_BASE_FILE || /\.test\.tsx?$/.test(r)) continue;
    for (const { line, match } of findApiLiterals(readFileSync(f, "utf8"))) {
      problems.push(`${r}:${line}: ${match} - build API URLs with apiUrl() from src/api/base.ts`);
    }
  }

  // 2. No IP addresses (the production one nowhere at all).
  const androidRoot = join(root, "android");
  const publicAssets = join(androidRoot, "app", "src", "main", "assets", "public");
  const androidFiles = walk(androidRoot, ANDROID_SKIP).filter((f) => !f.startsWith(publicAssets) && !BINARY.test(f));
  const scanned = [...srcFiles, join(root, "capacitor.config.ts"), ...androidFiles].filter((f) => existsSync(f));
  for (const f of scanned) {
    const r = rel(f);
    const strictOnly = /\.test\.tsx?$/.test(r);
    for (const { line, match } of findIpLiterals(readFileSync(f, "utf8"), { strictOnly })) {
      problems.push(`${r}:${line}: IP address ${match} - use the domain (ATP_APP_DOMAIN, MA-1), never an IP`);
    }
  }

  // 3. The build settings from the environment (the same rules capacitor.config.ts and vite.config.ts apply).
  let build = null;
  try {
    build = resolveNativeBuild(process.env, APP_ID);
  } catch (e) {
    problems.push(e instanceof Error ? e.message : String(e));
  }

  // 3b. The GitHub variable the internal / release builds will read (MA-1), when it is set: a host name whose https
  // origin passes the same rules. A debug build never uses it (ADR-0028 decision 10).
  const domain = String(process.env.ATP_APP_DOMAIN ?? "").trim();
  if (domain) {
    try {
      problems.push(...apiBaseProblems(apiBaseFromDomain(domain)).map((p) => `ATP_APP_DOMAIN: ${p}`));
    } catch (e) {
      problems.push(e instanceof Error ? e.message : String(e));
    }
  }

  // 4. One package id; no secrets in the Android project.
  const gradlePath = join(androidRoot, "app", "build.gradle");
  if (existsSync(gradlePath)) problems.push(...appIdProblems(readFileSync(gradlePath, "utf8")));
  else problems.push("android/app/build.gradle is missing (npx cap add android).");
  const mainDir = join(androidRoot, "app", "src", "main");
  const stringsPath = join(mainDir, "res", "values", "strings.xml");
  if (existsSync(stringsPath)) problems.push(...stringsProblems(readFileSync(stringsPath, "utf8")));
  const javaDir = join(mainDir, "java");
  const sources = walk(javaDir).map((f) => ({ path: relative(javaDir, f).split(sep).join("/"), text: readFileSync(f, "utf8") }));
  if (existsSync(javaDir)) problems.push(...activityProblems(sources));
  for (const f of committable(root, "android")) {
    if (isForbiddenAndroidFile(f)) problems.push(`${f}: keystores, google-services.json and local.properties never live in the repository`);
  }

  // 5. With --bundle: dist/ is the app's bundle for exactly these settings.
  if (process.argv.includes("--bundle") && build) {
    const markerPath = join(root, "dist", NATIVE_BUILD_MARKER);
    const marker = existsSync(markerPath) ? JSON.parse(readFileSync(markerPath, "utf8")) : null;
    problems.push(...bundleProblems(marker, build));
  }

  for (const p of problems) console.error(p);
  const summary = build ? `${build.kind} build, package ${build.appId}, API ${build.apiBase}` : "build settings refused";
  console.log(`mobile config check: ${srcFiles.length} source file(s), ${androidFiles.length} Android file(s); ${summary}; ${problems.length} problem(s).`);
  process.exit(problems.length ? 1 : 0);
}

if (isMain(process.argv[1], fileURLToPath(import.meta.url))) main();

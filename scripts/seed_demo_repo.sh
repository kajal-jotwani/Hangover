#!/usr/bin/env bash
# Build the demo_repo git history with decision-encoding commits, plus a
# `violation` branch (planted contradiction) and a `benign` branch (control).
# Idempotent: wipes and rebuilds demo_repo on every run.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)/demo_repo"

rm -rf "$REPO"
mkdir -p "$REPO/src/lib" "$REPO/src/utils" "$REPO/src/services"
cd "$REPO"
git init -q
git config user.email "demo@codemind.dev"
git config user.name "CodeMind Demo"
git config commit.gpgsign false

cat > package.json <<'JSON'
{ "name": "demo-service", "version": "1.0.0", "private": true }
JSON

# --------------------------------------------------------------------------
# D1 — apiClient over fetch
# --------------------------------------------------------------------------
cat > src/lib/apiClient.ts <<'TS'
// Central HTTP wrapper. All network calls MUST go through this.
export async function apiClient(url: string, opts: RequestInit = {}) {
  const authed = { ...opts, headers: { Authorization: `Bearer ${getToken()}`, ...opts.headers } };
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(url, authed);
      if (res.ok) return res;
      if (res.status >= 500 && attempt < 2) continue;
      throw new Error(`HTTP ${res.status}`);
    } catch (err) {
      if (attempt < 2) continue;
      throw err;
    }
  }
}
function getToken(): string { return process.env.API_TOKEN ?? ""; }
TS
git add -A
git commit -q -m "Switch all HTTP calls to use apiClient wrapper" -m "Direct fetch() calls bypassed our retry/auth logic and caused the Jan 14 outage. From now on ALL network calls must go through src/lib/apiClient.ts. Do not call fetch() directly in application code — routing through apiClient is mandatory so retries and auth headers are never skipped."

# --------------------------------------------------------------------------
# D2 — Redis cache, not in-memory
# --------------------------------------------------------------------------
cat > src/lib/cache.ts <<'TS'
import { createClient } from "redis";
const redis = createClient({ url: process.env.REDIS_URL });
redis.on("error", (e) => console.error("redis", e));
// Cache layer is Redis. Do NOT reintroduce in-memory Maps for caching —
// multiple instances made stale data in the Mar 2 incident.
export async function cacheGet(key: string) { return redis.get(key); }
export async function cacheSet(key: string, val: string, ttl = 300) { await redis.set(key, val, { EX: ttl }); }
TS
git add -A
git commit -q -m "Use Redis for the cache layer, drop in-memory cache" -m "The cache layer must be Redis (src/lib/cache.ts). In-memory Map caches are banned because we run multiple instances and a per-process cache serves stale data — that caused the Mar 2 stale-config incident. Never reintroduce an in-memory cache; always go through cacheGet/cacheSet."

# --------------------------------------------------------------------------
# D3 — don't simplify legacyRegex
# --------------------------------------------------------------------------
cat > src/utils/legacyRegex.ts <<'TS'
// DO NOT SIMPLIFY THIS REGEX. It looks redundant but the alternation handles
// a legacy customer format (Acme Corp, onboarded 2019) that escapes delimiters
// differently. Simplifying it breaks Acme imports silently.
export const LEGACY_LINE = /^(?:([^,|]+)[,|])?(?:"([^"]*)"|'([^']*)')\s*$/;
export function parseLegacy(line: string) { return LEGACY_LINE.exec(line); }
TS
git add -A
git commit -q -m "Document legacyRegex: do not simplify" -m "src/utils/legacyRegex.ts must not be simplified or refactored. The regex looks redundant but its alternation handles a legacy customer format (Acme Corp, onboarded 2019) that escapes delimiters differently. Simplifying it breaks Acme imports silently. Leave it as-is even during cleanups."

# --------------------------------------------------------------------------
# D4 — structured JSON logger, not console.log
# --------------------------------------------------------------------------
cat > src/lib/logger.ts <<'TS'
import { pino } from "pino";
// All logging must go through this structured logger. Do not use console.log
// in application code — unstructured logs broke our log aggregator alerts.
export const logger = pino({ level: process.env.LOG_LEVEL ?? "info" });
TS
git add -A
git commit -q -m "Adopt structured JSON logger, ban console.log" -m "All logging must go through src/lib/logger.ts (pino, structured JSON). console.log is banned in application code because unstructured logs broke our log-aggregator alerts on May 9. Always use the shared logger so log shape stays consistent."

# a service that uses apiClient correctly (the "good" baseline)
cat > src/services/userService.ts <<'TS'
import { apiClient } from "../lib/apiClient";
export async function getUser(id: string) {
  return apiClient(`/users/${id}`);
}
TS
git add -A
git commit -q -m "Add userService using apiClient" -m "userService fetches users through apiClient as required."

git branch -M main

# --------------------------------------------------------------------------
# violation branch — planted contradiction (D2): in-memory Map cache
# --------------------------------------------------------------------------
git checkout -q -b violation
cat > src/lib/cache.ts <<'TS'
// Drop the Redis dependency — a per-process Map is simpler and faster.
const mem = new Map<string, string>();
export async function cacheGet(key: string) { return mem.get(key) ?? null; }
export async function cacheSet(key: string, val: string, ttl = 300) { mem.set(key, val); }
TS
git add -A
git commit -q -m "Replace Redis cache with in-memory Map for speed" -m "Drop the Redis dependency in src/lib/cache.ts and use a per-process in-memory Map cache instead — simpler, no network hop, lower latency."

# --------------------------------------------------------------------------
# benign branch — control: a non-violating change to the SAME file
# --------------------------------------------------------------------------
git checkout -q main
git checkout -q -b benign
cat > src/lib/cache.ts <<'TS'
import { createClient } from "redis";
const redis = createClient({ url: process.env.REDIS_URL });
redis.on("error", (e) => console.error("redis", e));
// Cache layer is Redis. Do NOT reintroduce in-memory Maps for caching —
// multiple instances made stale data in the Mar 2 incident.
/** Default TTL in seconds for cache entries. */
const DEFAULT_TTL = 300;
export async function cacheGet(key: string) { return redis.get(key); }
export async function cacheSet(key: string, val: string, ttl = DEFAULT_TTL) { await redis.set(key, val, { EX: ttl }); }
TS
git add -A
git commit -q -m "Extract DEFAULT_TTL constant in cache.ts" -m "Refactor: pull the magic 300 into a named DEFAULT_TTL constant. No behavior change — still Redis-backed."

git checkout -q main
echo "Seeded demo_repo at $REPO"
git --no-pager log --oneline --all
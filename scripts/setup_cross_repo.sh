#!/usr/bin/env bash
# CodeMind — cross-repo shared-memory demo (repo B setup).
#
# WHY THIS EXISTS: the Cognee Cloud graph is TENANT-GLOBAL. A decision remembered
# in repo A is retrievable from repo B's CI run — org-wide shared memory, which is
# impossible with self-hosted/local memory and is CodeMind's headline differentiator.
#
# This script wires up a SECOND repo (repo B) to the same Cognee tenant as repo A
# (this repo), with NO memory_registry.json — so repo B's only memory is the shared
# Cloud graph. Then it opens a PR on repo B that repeats the Redis→Map mistake.
# CodeMind catches it citing "Cache layer must be Redis" — a decision remembered in
# repo A. That's the cross-repo beat.
#
# USAGE (the user runs this — repo creation crosses a trust boundary the agent
# won't do autonomously):
#   1. Create repo B (empty, private is fine):
#        gh repo create <YOU>/codemind-cross-b --private
#      (or via https://github.com/new)
#   2. From this repo's root:
#        bash scripts/setup_cross_repo.sh <YOU>/codemind-cross-b
#   3. Watch the PR on repo B — the CodeMind bot comment cites the Redis decision
#      remembered in repo A, and a red CodeMind / memory check appears.
#
# REQUIRES: gh authed; local .env with the Cognee + Ollama creds (same tenant as
# repo A). Reads secrets from .env WITHOUT printing them.
set -euo pipefail

REPO_B="${1:?usage: setup_cross_repo.sh <owner/repo-b>}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Building repo B working tree from $HERE (no .env, no registry, no local artifacts)"
TMP="$(mktemp -d)"
rsync -a --exclude='.git' --exclude='.env' --exclude='.venv' --exclude='demo_repo' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.cognee' \
  --exclude='dashboard/index.html' --exclude='pending_conflict.json' \
  --exclude='memory_registry.json' --exclude='event_log.json' --exclude='.DS_Store' \
  "$HERE/" "$TMP/"

# repo B baseline: a Redis cache.ts (the "before" the PR modifies).
mkdir -p "$TMP/scratch"
cat > "$TMP/scratch/cache.ts" <<'TS'
// scratch/cache.ts — cache layer backed by Redis.
// Team decision: the cache layer MUST use Redis; never an in-memory Map.
import { createClient } from "redis";
const redis = createClient({ url: process.env.REDIS_URL });
await redis.connect();
export async function cacheGet(key: string): Promise<string | null> { return redis.get(key); }
export async function cacheSet(key: string, value: string): Promise<void> { await redis.set(key, value); }
TS

cat > "$TMP/CROSSREPO.md" <<'MD'
# codemind-cross-b (repo B — cross-repo demo)
This repo is wired to the SAME Cognee Cloud tenant as repo A and has NO
`memory_registry.json`. Its only memory is the shared Cloud graph that repo A
populated. A PR here repeating the Redis→Map mistake gets caught citing a
decision remembered in repo A — org-wide shared memory.
MD

echo "==> Initializing git + pushing to $REPO_B main"
git -C "$TMP" init -q
git -C "$TMP" -c user.name="Divy Singhvi" -c user.email="divysinghvi5@gmail.com" add -A
git -C "$TMP" -c user.name="Divy Singhvi" -c user.email="divysinghvi5@gmail.com" commit -q -m "codemind-cross-b: repo B for the cross-repo shared-memory demo (same Cognee tenant as repo A, no local registry)"
git -C "$TMP" branch -M main
git -C "$TMP" remote add origin "https://github.com/$REPO_B.git"
git -C "$TMP" push -f origin main

echo "==> Setting Cognee + Ollama secrets on $REPO_B (same tenant as repo A; values not printed)"
PY="$HERE/.venv/bin/python"
if [ ! -x "$PY" ]; then PY="python3"; fi
pushd "$TMP" >/dev/null
"$PY" -m codemind.cli link --repo "$REPO_B" --no-workflows
popd >/dev/null

echo "==> Creating the violation branch (Redis → in-memory Map) + opening the PR"
git -C "$TMP" checkout -b violation
cat > "$TMP/scratch/cache.ts" <<'TS'
// scratch/cache.ts — cache layer backed by an in-memory Map.
// Switched from Redis to a per-process Map for simplicity (single-instance deploy).
const store = new Map<string, string>();
export async function cacheGet(key: string): Promise<string | null> { return store.get(key) ?? null; }
export async function cacheSet(key: string, value: string): Promise<void> { store.set(key, value); }
TS
git -C "$TMP" -c user.name="Divy Singhvi" -c user.email="divysinghvi5@gmail.com" commit -q -am "switch cache from Redis to in-memory Map"
git -C "$TMP" push -u origin violation

PR_URL=$(gh pr create --repo "$REPO_B" --base main --head violation \
  --title "switch cache from Redis to in-memory Map" \
  --body "Cross-repo demo. This repo has no local memory registry — CodeMind should catch this using the SHARED Cognee Cloud graph populated by repo A, citing the 'Cache layer must be Redis' decision remembered there.")

echo
echo "==> Done. Watch the PR: $PR_URL"
echo "    The CodeMind bot comment + red check should appear within ~2 min, citing a"
echo "    decision remembered in repo A — proving org-wide shared memory."
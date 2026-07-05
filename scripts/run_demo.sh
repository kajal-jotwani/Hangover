#!/usr/bin/env bash
# The scripted 2-minute demo. Run AFTER scripts/setup.sh.
# Press ENTER at each beat to advance (lets the presenter narrate).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
REPO="$ROOT/demo_repo"

beat() { echo; echo "===================================================================="; echo "  ▶ $1"; echo "===================================================================="; }
pause() { read -r -p "  [press ENTER to continue] " _; }

beat "0:00–0:20  THE PROBLEM"
echo "  Every team loses why-code-is-the-way-it-is when people leave."
echo "  CodeMind gives your repo a memory — and makes it argue back."
echo "  This isn't a linter. It's memory that can be wrong, get challenged,"
echo "  and correct itself. Watch it catch a teammate breaking a decision — live."
pause

beat "0:20–0:40  THE SEEDED MEMORY (the 'before' answer)"
echo "  Cognee Cloud holds our team's durable decisions. Ask the repo a question:"
echo "  > 'can I use an in-memory Map cache instead of Redis?'"
"$PY" -c "
import asyncio
from codemind.runtime import cognee_client
async def go():
    await cognee_client.connect()
    for a in await cognee_client.recall_decisions('can I use an in-memory Map cache instead of Redis?'):
        print('   ', a[:260])
    await cognee_client.disconnect()
asyncio.run(go())
"
echo "  ^ That is the OLD belief: 'no, must use Redis'. Remember it — we'll compare after reconciliation."
pause

beat "0:40–1:10  A TEAMMATE COMMITS A CHANGE THAT BREAKS THE DECISION (live)"
echo "  On stage: create a branch and commit an in-memory Map cache in cache.ts."
cd "$REPO"
git checkout -q main
git checkout -q -b demo-live
cat > src/lib/cache.ts <<'TS'
// Drop the Redis dependency — a per-process Map is simpler and faster.
const mem = new Map<string, string>();
export async function cacheGet(key: string) { return mem.get(key) ?? null; }
export async function cacheSet(key: string, val: string, ttl = 300) { mem.set(key, val); }
TS
git add -A
git commit -q -m "Replace Redis cache with in-memory Map for speed" -m "Drop the Redis dependency in src/lib/cache.ts and use a per-process in-memory Map cache instead — simpler, no network hop, lower latency."
cd "$ROOT"
echo "  Committed. Now CodeMind's contradiction agent runs over the diff:"
echo
"$PY" -m codemind.runtime.contradiction --repo demo_repo --branch demo-live
pause

beat "1:10–1:30  RECONCILIATION — confirm the change is intentional"
echo "  The teammate confirms: yes, intentional. CodeMind revises its belief:"
echo "    remember() the UPDATE  ->  forget() the old memory (surgical)  ->  improve()"
"$PY" -m codemind.runtime.reconcile confirm --reason "we moved to a single-instance deployment, so a per-process cache no longer serves stale data"
pause

beat "1:30–1:50  THE PROOF — a new teammate asks, and gets the CURRENT answer"
echo "  Second terminal asks the SAME question: 'can I use an in-memory Map cache instead of Redis?'"
echo "  The answer has CHANGED:"
"$PY" -c "
import asyncio
from codemind.runtime import cognee_client
async def go():
    await cognee_client.connect()
    for a in await cognee_client.recall_decisions('can I use an in-memory Map cache instead of Redis?'):
        print('   ', a[:260])
    await cognee_client.disconnect()
asyncio.run(go())
"
echo "  ^ Compare to the 'before' answer at 0:20 — 'no, must use Redis' is now 'yes, permitted'."
echo "  The before/after flip is the loop closing: the memory corrected itself, live."
pause

beat "1:50–2:00  CLOSE"
echo "  This isn't a snapshot. It's memory that can be wrong, get challenged,"
echo "  and correct itself — the way a real teammate's understanding would."
echo "  remember. recall. improve. forget. — the full Cognee lifecycle, live."
echo
echo "  (demo_repo live branch left on demo-live; reset with scripts/setup.sh)"
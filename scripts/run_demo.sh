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
echo "  Cognee Cloud holds our team's durable decisions. Recall why we use apiClient:"
"$PY" -c "
import asyncio, cognee_client
async def go():
    await cognee_client.connect()
    for a in await cognee_client.recall_decisions('why do we use apiClient for HTTP calls'):
        print('   ', a[:220])
    await cognee_client.disconnect()
asyncio.run(go())
"
echo "  ^ That is the OLD belief. Remember it — we'll compare after the reconciliation."
pause

beat "0:40–1:10  A TEAMMATE COMMITS A CHANGE THAT BREAKS THE DECISION (live)"
echo "  On stage: create a branch and commit a direct fetch() call in userService."
cd "$REPO"
git checkout -q main
git checkout -q -b demo-live
cat > src/services/userService.ts <<'TS'
export async function getUser(id: string) {
  // direct fetch — faster, no wrapper overhead
  const res = await fetch(`/users/${id}`);
  return res.json();
}
TS
git add -A
git commit -q -m "Inline fetch in userService for speed" -m "Skip the apiClient wrapper and call fetch() directly to reduce overhead."
cd "$ROOT"
echo "  Committed. Now CodeMind's contradiction agent runs over the diff:"
echo
"$PY" contradiction.py --repo demo_repo --branch demo-live
pause

beat "1:10–1:30  RECONCILIATION — confirm the change is intentional"
echo "  The teammate confirms: yes, intentional. CodeMind revises its belief:"
echo "    remember() the UPDATE  ->  forget() the old memory (surgical)  ->  improve()"
"$PY" reconcile.py confirm --reason "we added a global fetch shim with its own retry/auth, so the wrapper rule no longer applies"
pause

beat "1:30–1:50  THE PROOF — a new teammate asks, and gets the CURRENT answer"
echo "  Second terminal: 'why do we use apiClient?' -> the answer has CHANGED."
"$PY" -c "
import asyncio, cognee_client
async def go():
    await cognee_client.connect()
    for a in await cognee_client.recall_decisions('why do we use apiClient for HTTP calls'):
        print('   ', a[:240])
    await cognee_client.disconnect()
asyncio.run(go())
"
echo "  ^ Compare to the 'before' answer at 0:20 — the delta is the loop closing."
pause

beat "1:50–2:00  CLOSE"
echo "  This isn't a snapshot. It's memory that can be wrong, get challenged,"
echo "  and correct itself — the way a real teammate's understanding would."
echo "  remember. recall. improve. forget. — the full Cognee lifecycle, live."
echo
echo "  (demo_repo live branch left on demo-live; reset with scripts/setup.sh)"
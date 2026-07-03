#!/usr/bin/env bash
# One-time prep BEFORE the camera rolls: rebuild demo_repo, ingest decisions
# into the Cognee dataset, and confirm the 'before' recall answer. Run this
# once, then run_demo.sh live. Re-runnable: --reset surgically forgets every
# data_id in the registry (including any prior UPDATE) so the graph is clean.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"

echo "==> 1. Rebuild demo_repo with seeded decisions"
bash scripts/seed_demo_repo.sh

echo
echo "==> 2. Ingest decisions into Cognee Cloud (Ollama Cloud for extraction + Cognee)"
"$PY" ingest.py --repo demo_repo --reset

echo
echo "==> 3. Pre-demo recall check (the 'before' answer the demo will compare against)"
"$PY" -c "
import asyncio, cognee_client
from config import check_keys
check_keys(need_cognee=True)
async def go():
    await cognee_client.connect()
    ans = await cognee_client.recall_decisions('can I use an in-memory Map cache instead of Redis?')
    print('recall ->')
    for a in ans: print('  -', a[:220])
    await cognee_client.disconnect()
asyncio.run(go())
"
echo
echo "Setup complete. Now run:  bash scripts/run_demo.sh"
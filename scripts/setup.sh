#!/usr/bin/env bash
# One-time prep BEFORE the camera rolls: rebuild demo_repo, ingest decisions
# into Cognee, and confirm recall works. Run this once, then run_demo.sh live.
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
echo "==> 3. Pre-demo recall check"
"$PY" -c "
import asyncio, cognee_client
from config import check_keys
check_keys(need_cognee=True)
async def go():
    await cognee_client.connect()
    ans = await cognee_client.recall_decisions('why do we use apiClient for HTTP calls')
    print('recall ->')
    for a in ans: print('  -', a[:200])
    await cognee_client.disconnect()
asyncio.run(go())
"
echo
echo "Setup complete. Now run:  bash scripts/run_demo.sh"
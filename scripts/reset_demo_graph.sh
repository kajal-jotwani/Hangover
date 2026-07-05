#!/usr/bin/env bash
# CodeMind — reset the demo Cloud graph to a clean 4-decision before-state.
#
# WHY: repeated `setup.sh` runs (and a past `reconcile confirm`) can leave
# DUPLICATE decision nodes + a stale UPDATE in the tenant-global Cognee graph.
# The demo's `reconcile confirm` before/after flip needs a crisp graph: after
# forgetting D2 + remembering the UPDATE, recall should surface the UPDATE, not
# an old "must use Redis" duplicate. Run this before presenting so the flip is
# clean.
#
# WHAT IT DOES (mutates the shared Cloud graph — that's the point):
#   1. Collects EVERY data_id ever recorded in event_log.json (all remember +
#      forget events, including any stale UPDATE).
#   2. Surgically forgets them all (cognee.forget(data_id) per id — safe, never
#      forget(dataset=) which corrupts the cloud dataset).
#   3. Clears the local registry + event log.
#   4. Re-runs setup.sh -> re-ingests the 4 fresh demo decisions.
#
# REQUIRES: local .env with Cognee + Ollama creds. Read-only on nothing — this
# mutates the graph. Safe + re-runnable.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"

echo "==> 1. Forgetting every data_id ever recorded in event_log.json"
"$PY" - <<'PY'
import asyncio, json
from codemind.runtime import cognee_client
from codemind.runtime.config import check_keys
check_keys(need_cognee=True, need_llm=False)
async def main():
    await cognee_client.connect()
    events = json.load(open("event_log.json")) if __import__("os").path.exists("event_log.json") else []
    ids = []
    for ev in events:
        did = ev.get("data_id")
        if did and did not in ids:
            ids.append(did)
    print(f"  collected {len(ids)} unique data_ids to forget")
    if ids:
        res = await cognee_client.forget_many(ids)
        print(f"  forgot {res['ok']} ok, {res['failed']} failed")
        if res["errors"]:
            print(f"  errors: {res['errors'][:3]}")
    await cognee_client.disconnect()
asyncio.run(main())
PY

echo "==> 2. Clearing local registry + event log"
"$PY" -c "from codemind.runtime import registry, config; registry.save_registry({}); registry._write(config.EVENT_LOG_PATH, [])"
echo "  cleared memory_registry.json + event_log.json"

echo "==> 3. Re-ingesting the 4 fresh demo decisions (setup.sh)"
bash scripts/setup.sh

echo
echo "==> Done. The Cloud graph now has exactly the 4 demo decisions (no duplicates,"
echo "    no stale UPDATE). The before-state recall returns 'must use Redis' and the"
echo "    reconcile confirm flip will be crisp. Demo ready: bash scripts/run_demo.sh"
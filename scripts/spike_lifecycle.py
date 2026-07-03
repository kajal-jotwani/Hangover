"""Spike the deeper Cognee lifecycle APIs against the live cloud tenant.

Focus on the three high-value targets for 'lean harder on Cognee':
  1. cognee.search(include_references, neighborhood_depth, feedback_influence, only_context)
  2. cognee.visualize(path, dataset)  -> real graph HTML
  3. cognee.memify(dataset)  -> enrichment (mutating; guarded, last)
Plus cognee.datasets.list_datasets() (no-arg, read-only).
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

import cognee
from cognee_client import connect, disconnect
from config import DATASET_NAME, check_keys


def _ser(x, limit=500):
    try:
        s = json.dumps(x, default=str)
    except Exception:
        s = repr(x)
    return s[:limit]


async def _t(label, coro_fn, timeout=120):
    """Run coro_fn() (a zero-arg factory so we can build the coro inside the try)."""
    print(f"\n=== {label} ===")
    try:
        r = await asyncio.wait_for(coro_fn(), timeout=timeout)
        print("OK ->", _ser(r))
        return r
    except asyncio.TimeoutError:
        print(f"TIMEOUT after {timeout}s")
    except Exception as e:
        print(f"FAIL -> {type(e).__name__}: {str(e)[:300]}")
    return None


async def main() -> None:
    check_keys(need_cognee=True)
    await connect()
    print(f"connected. dataset={DATASET_NAME}")

    # 0. list_datasets (read-only, no args)
    await _t("0 datasets.list_datasets()", lambda: cognee.datasets.list_datasets(), timeout=60)

    q = "can I use an in-memory Map cache instead of Redis?"

    # 1a. search with graph params (include_references + neighborhood + feedback)
    await _t("1a cognee.search(include_references, neighborhood_depth=2, feedback_influence=0.5)",
             lambda: cognee.search(query_text=q, datasets=[DATASET_NAME], top_k=5,
                                   include_references=True, neighborhood_depth=2,
                                   feedback_influence=0.5),
             timeout=90)
    # 1b. search only_context (raw graph context, no LLM answer) — shows the retrieved nodes
    await _t("1b cognee.search(only_context=True)",
             lambda: cognee.search(query_text=q, datasets=[DATASET_NAME], top_k=5,
                                   only_context=True),
             timeout=90)
    # 1c. plain search for baseline comparison
    await _t("1c cognee.search() baseline",
             lambda: cognee.search(query_text=q, datasets=[DATASET_NAME], top_k=5),
             timeout=90)

    # 2. visualize -> HTML
    out = os.path.join(tempfile.gettempdir(), "codemind_graph.html")
    await _t(f"2 cognee.visualize() -> {out}",
             lambda: cognee.visualize(destination_file_path=out, dataset=DATASET_NAME),
             timeout=90)
    if os.path.exists(out):
        print(f"   file size: {os.path.getsize(out)} bytes")

    # 3. memify (mutating enrichment — last, guarded)
    await _t("3 cognee.memify(dataset)",
             lambda: cognee.memify(dataset=DATASET_NAME, run_in_background=False),
             timeout=180)

    await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
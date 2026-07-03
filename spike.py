"""Phase-0 spike — de-risks the thesis-critical verb BEFORE we build on it.

Verifies, against the live Cognee Cloud tenant:
  1. serve() connects.
  2. remember() returns something we can pull a per-item data_id from.
  3. forget(data_id=..., dataset=...) surgically deletes ONE memory (not the whole dataset).
  4. improve() runs cleanly.
  5. (probe) whether cognee.update() exists and is usable for in-place reconciliation.

Run:  python spike.py
Needs: COGNEE_URL, COGNEE_API_KEY in .env
"""
from __future__ import annotations

import asyncio
import inspect

import cognee

import cognee_client
from config import DATASET_NAME, check_keys


def _shape(label: str, obj: object) -> None:
    print(f"\n=== {label} ===")
    print(f"type: {type(obj).__name__}")
    print(f"repr: {repr(obj)[:800]}")
    if hasattr(obj, "model_dump"):
        try:
            print(f"model_dump: {obj.model_dump()}")
        except Exception as e:
            print(f"model_dump failed: {e}")
    if isinstance(obj, dict):
        print(f"keys: {list(obj.keys())}")


async def main() -> None:
    check_keys(need_cognee=True, need_anthropic=False)
    print(f"Connecting to Cognee Cloud, dataset={DATASET_NAME} ...")
    await cognee_client.connect()
    print("connected.\n")

    # 1. remember two tiny facts into a SPIKE-specific dataset (don't pollute the real one)
    spike_ds = "codemind_spike"
    print(f"remembering two facts into dataset={spike_ds} ...")
    r1 = await cognee.remember("Decision A: always use apiClient for HTTP.", dataset_name=spike_ds)
    r2 = await cognee.remember("Decision B: cache layer is Redis.", dataset_name=spike_ds)
    _shape("remember() return #1", r1)
    _shape("remember() return #2", r2)

    # Try to extract per-item data_ids via the defensive helper on real results.
    # (cognee_client wrapper targets DATASET_NAME, so inline the same logic here.)
    id1 = cognee_client._extract_data_id(r1)
    id2 = cognee_client._extract_data_id(r2)
    ds_id = cognee_client._extract_dataset_id(r1)
    print(f"\nextracted data_id #1: {id1}")
    print(f"extracted data_id #2: {id2}")
    print(f"extracted dataset_id : {ds_id}")

    # 2. recall to confirm both are queryable
    print("\nrecall('cache') ->")
    answers = await cognee.recall(query_text="what is the cache layer", datasets=[spike_ds], top_k=5)
    for a in answers or []:
        print(f"  - {repr(a)[:300]}")

    # 3. surgical forget of ONE item
    target = id1 or id2
    if target:
        print(f"\nforget(data_id={target}, dataset={spike_ds}) ...")
        fres = await cognee.forget(data_id=target, dataset=spike_ds)
        _shape("forget() return", fres)
        print("\nrecall('apiClient') after forgetting one ->")
        ans2 = await cognee.recall(query_text="apiClient HTTP", datasets=[spike_ds], top_k=5)
        for a in ans2 or []:
            print(f"  - {repr(a)[:300]}")
        print("\n>>> If only the forgotten topic is gone and the other remains, surgical forget WORKS.")
    else:
        print("\n!! Could not extract a per-item data_id from remember() output.")
        print("    Inspect the repr above and update _extract_data_id() in cognee_client.py.")
        print("    Fallback plan: append 'superseded' memory + rely on improve() re-weighting.")

    # 4. improve()
    print("\nimprove() ...")
    try:
        ires = await cognee.improve(dataset_name=spike_ds)
        _shape("improve() return", ires)
    except Exception as e:
        print(f"improve() raised: {e}")

    # 5. probe update()
    print("\nprobing cognee.update ...")
    if hasattr(cognee, "update"):
        try:
            sig = inspect.signature(cognee.update)
            print(f"cognee.update signature: {sig}")
            print(">>> update() EXISTS — candidate for in-place reconciliation (confirm semantics in docs).")
        except (ValueError, TypeError) as e:
            print(f"could not inspect update(): {e}")
    else:
        print("cognee.update not present — use remember-new + forget-old for reconciliation.")

    # cleanup: wipe the spike dataset so reruns are clean
    print("\ncleanup: forget(dataset=spike_ds) ...")
    try:
        await cognee.forget(dataset=spike_ds)
        print("spike dataset wiped.")
    except Exception as e:
        print(f"cleanup forget(dataset) raised: {e}")

    await cognee_client.disconnect()
    print("\nSpike done.")


if __name__ == "__main__":
    asyncio.run(main())
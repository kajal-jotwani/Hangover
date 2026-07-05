"""Cognee lifecycle API spike — exercised by ``codemind doctor --cognee``.

Probes the higher-value Cognee verbs (search with graph params, visualize,
memify) against the live cloud tenant and returns a concise line-by-line
report. Inlined into the package (rather than shelled out to a sibling script)
so it works from a wheel install, not just an editable checkout.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import cognee

from codemind.runtime import cognee_client
from codemind.runtime.config import DATASET_NAME, check_keys


def _ser(x: Any, limit: int = 500) -> str:
    try:
        s = json.dumps(x, default=str)
    except Exception:
        s = repr(x)
    return s[:limit]


async def _t(lines: list[str], label: str, coro_fn, timeout: int = 120) -> None:
    """Run coro_fn() (a zero-arg factory so we can build the coro inside the try)."""
    lines.append(f"=== {label} ===")
    try:
        r = await asyncio.wait_for(coro_fn(), timeout=timeout)
        lines.append("OK -> " + _ser(r))
    except asyncio.TimeoutError:
        lines.append(f"TIMEOUT after {timeout}s")
    except Exception as e:
        lines.append(f"FAIL -> {type(e).__name__}: {str(e)[:300]}")


async def _main(lines: list[str]) -> None:
    check_keys(need_cognee=True)
    await cognee_client.connect()
    lines.append(f"connected. dataset={DATASET_NAME}")

    await _t(lines, "0 datasets.list_datasets()",
             lambda: cognee.datasets.list_datasets(), timeout=60)

    q = "can I use an in-memory Map cache instead of Redis?"

    await _t(lines, "1a cognee.search(include_references, neighborhood_depth=2, feedback_influence=0.5)",
             lambda: cognee.search(query_text=q, datasets=[DATASET_NAME], top_k=5,
                                   include_references=True, neighborhood_depth=2,
                                   feedback_influence=0.5),
             timeout=90)
    await _t(lines, "1b cognee.search(only_context=True)",
             lambda: cognee.search(query_text=q, datasets=[DATASET_NAME], top_k=5,
                                   only_context=True),
             timeout=90)
    await _t(lines, "1c cognee.search() baseline",
             lambda: cognee.search(query_text=q, datasets=[DATASET_NAME], top_k=5),
             timeout=90)

    out = os.path.join(tempfile.gettempdir(), "codemind_graph.html")
    await _t(lines, f"2 cognee.visualize() -> {out}",
             lambda: cognee.visualize(destination_file_path=out, dataset=DATASET_NAME),
             timeout=90)
    if os.path.exists(out):
        lines.append(f"   file size: {os.path.getsize(out)} bytes")

    await _t(lines, "3 cognee.memify(dataset)",
             lambda: cognee.memify(dataset=DATASET_NAME, run_in_background=False),
             timeout=180)

    await cognee_client.disconnect()


def run_lifecycle_spike(repo_root: Path | None = None) -> list[str]:
    """Run the lifecycle spike and return its report lines.

    Best-effort: any failure is captured as a FAIL line rather than raised, so
    ``doctor --cognee`` always produces a report.
    """
    lines: list[str] = []
    try:
        asyncio.run(_main(lines))
    except SystemExit as e:
        lines.append(f"FAIL -> SystemExit: {e}")
    except Exception as e:
        lines.append(f"FAIL -> {type(e).__name__}: {str(e)[:300]}")
    return lines
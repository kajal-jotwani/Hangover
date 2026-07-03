#!/usr/bin/env python3
"""CodeMind -> Cognee Cloud demo on a REAL big OSS repo (topoteretes/cognee).

Ingests a slice of cognee's ACTUAL commit history into a separate Cognee Cloud
dataset (codemind_cognee) so you can SEE the resulting memory graph: real
engineering decisions our pipeline extracts from cognee's git log, stored as
Cognee graph nodes, and retrievable via recall() + graph-node search().

This is the "eat your own dogfood" + scale proof: CodeMind built memory around
the very framework it runs on, and caught a real contradiction (async->sync
telemetry) in cognee's own history.

TENANT-GLOBAL CAVEAT (see memory: cognee-cloud-gotchas):
  Cognee Cloud's graph is tenant-global. These decisions are *tagged* under the
  codemind_cognee dataset, but recall/search across the tenant can surface them
  too, so they temporarily coexist with the demo (codemind_demo) graph. Run
  `--cleanup` to surgically forget every cognee data_id (captured in
  codemind_cognee_data_ids.json) and restore the tenant to its pre-cognee state.
  The demo's local memory_registry.json / event_log.json are NEVER touched.

Usage:
  python scripts/cognee_cloud_demo.py --repo /tmp/cognee_real --max-count 25
  python scripts/cognee_cloud_demo.py --cleanup        # forget all, restore demo tenant
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Run as `python scripts/cognee_cloud_demo.py` — put the project root on the
# path so the sibling modules (cognee_client, config, git_io, llm) resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.panel import Panel

import cognee_client
from config import check_keys
from git_io import log_commits
from llm import extract_decision

console = Console()
IDS_FILE = Path("codemind_cognee_data_ids.json")

# Queries that match real decisions we know cognee's recent history contains
# (verified via dry-run): telemetry sync/async, KuzuDB buffer pool, LLM gateway,
# engine-handle stability. Used to *show* the cloud graph content.
SHOW_QUERIES = [
    "telemetry queue worker async sync HTTP request",
    "KuzuDB buffer pool size graph database capacity",
    "LLM connectivity gateway preflight unified",
    "vector engine handle graph engine cache stable reference",
]


async def ingest_and_show(repo: str, max_count: int, dataset: str) -> None:
    check_keys(need_cognee=True, need_llm=True)
    cognee_client.DATASET_NAME = dataset  # separate dataset (tenant-global caveat above)
    await cognee_client.connect()

    commits = log_commits(repo, max_count=max_count)
    console.print(f"[bold]Ingesting {len(commits)} cognee commits into Cognee Cloud dataset[/bold] "
                  f"[cyan]{dataset}[/cyan]\n")

    ids: list[str] = []
    remembered = 0
    for i, c in enumerate(commits, 1):
        console.print(f"[{i}/{len(commits)}] {c.sha[:8]} {c.message.splitlines()[0][:65]}")
        d = extract_decision(c.message, c.diff)
        if not d:
            console.print("  [dim]no durable decision -> skip[/dim]")
            continue
        text = (f"Decision: {d['decision']}\nRationale: {d['rationale']}\n"
                f"Scope: {d['scope']}\nSource commit: {c.sha}")
        imp = max(0.5, min(0.99, d["confidence"]))
        res = await cognee_client.remember_decision(text, importance_weight=imp)
        if res["data_id"]:
            ids.append(res["data_id"])
        remembered += 1
        console.print(f"  [green]remembered[/green]: {d['decision'][:72]}")
        console.print(f"  [blue]data_id[/blue]: {res['data_id'] or '(none)'}")

    IDS_FILE.write_text(json.dumps(ids, indent=2))
    console.print(Panel.fit(
        f"Remembered {remembered} cognee decisions to dataset {dataset}.\n"
        f"Captured {len(ids)} data_id(s) -> {IDS_FILE} (for --cleanup).",
        title="Ingest complete", style="green"))

    console.print("\n[bold magenta]==== How it looks in the Cognee Cloud graph ====[/bold magenta]\n")
    for q in SHOW_QUERIES:
        console.print(Panel.fit(f"query: {q}", style="bold magenta"))
        console.print("[blue]recall()[/blue] — LLM answer synthesized over the cognee graph:")
        answers = await cognee_client.recall_decisions(q, top_k=5)
        for a in answers:
            console.print(f"  - {a[:260]}")
        if not answers:
            console.print("  [dim](no recall result)[/dim]")
        console.print("[blue]search(only_context=True)[/blue] — raw graph nodes (Decision + Rationale + tags):")
        nodes = await cognee_client.search_graph_nodes(q, top_k=6)
        for n in nodes:
            console.print(f"  • {n[:260]}")
        if not nodes:
            console.print("  [dim](no graph nodes)[/dim]")
        console.print()

    await cognee_client.disconnect()
    console.print(Panel.fit(
        "Done. To restore the demo tenant (forget every cognee data_id), run:\n"
        "  python scripts/cognee_cloud_demo.py --cleanup",
        title="Tip", style="yellow"))


async def cleanup(dataset: str) -> None:
    check_keys(need_cognee=True, need_llm=False)
    cognee_client.DATASET_NAME = dataset
    await cognee_client.connect()
    ids = json.loads(IDS_FILE.read_text()) if IDS_FILE.exists() else []
    console.print(f"[yellow]Surgically forgetting {len(ids)} cognee data_id(s) from the cloud graph...[/yellow]")
    if ids:
        res = await cognee_client.forget_many(ids)
        console.print(f"  forgot {res['ok']} ok, {res['failed']} failed")
        if res["errors"]:
            console.print(f"  errors: {res['errors'][:3]}")
    else:
        console.print("  [dim]no data_ids recorded — nothing to forget.[/dim]")
    IDS_FILE.unlink(missing_ok=True)
    await cognee_client.disconnect()
    console.print(Panel.fit("Cloud graph restored to its pre-cognee state. Demo tenant clean.",
                            title="Cleanup complete", style="yellow"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/tmp/cognee_real",
                    help="path to a clone of topoteretes/cognee (or any repo)")
    ap.add_argument("--max-count", type=int, default=25,
                    help="number of recent commits to walk + ingest")
    ap.add_argument("--dataset", default="codemind_cognee",
                    help="Cognee Cloud dataset to ingest into (separate from codemind_demo)")
    ap.add_argument("--cleanup", action="store_true",
                    help="forget every data_id in codemind_cognee_data_ids.json + exit")
    args = ap.parse_args()
    if args.cleanup:
        asyncio.run(cleanup(args.dataset))
    else:
        asyncio.run(ingest_and_show(args.repo, args.max_count, args.dataset))


if __name__ == "__main__":
    main()
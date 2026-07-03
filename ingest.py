"""Phase 1 — walk demo_repo history, extract durable decisions, remember() them
into Cognee, and record each memory's data_id in the registry.

Usage:
  python ingest.py --repo demo_repo
  python ingest.py --repo demo_repo --reset   # forget the dataset first
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from rich.console import Console
from rich.panel import Panel

import cognee_client
import registry
from config import DATASET_NAME, DEMO_REPO, check_keys
from git_io import log_commits
from llm import extract_decision

console = Console()


async def ingest(repo_path: str, *, reset: bool) -> None:
    check_keys(need_cognee=True, need_anthropic=True)
    await cognee_client.connect()

    if reset:
        console.print("[yellow]Forgetting entire dataset before ingest...[/yellow]")
        try:
            await cognee_client.forget_dataset()
        except Exception as e:
            console.print(f"[dim]forget_dataset: {e}[/dim]")
        registry.save_registry({})

    commits = log_commits(repo_path)
    console.print(f"Found {len(commits)} commits in [cyan]{repo_path}[/cyan]\n")

    remembered = 0
    for i, commit in enumerate(commits, 1):
        console.print(f"[bold][{i}/{len(commits)}][/bold] {commit.sha[:8]} {commit.message.splitlines()[0][:70]}")
        decision = extract_decision(commit.message, commit.diff)
        if not decision:
            console.print("  [dim]no durable decision -> skip[/dim]\n")
            continue
        importance = max(0.5, min(0.99, decision["confidence"]))
        text = (
            f"Decision: {decision['decision']}\n"
            f"Rationale: {decision['rationale']}\n"
            f"Scope: {decision['scope']}\n"
            f"Source commit: {commit.sha}"
        )
        console.print(f"  [green]decision:[/green] {decision['decision'][:80]}")
        console.print(f"  [green]scope:[/green] {decision['scope'][:80]}")
        res = await cognee_client.remember_decision(text, importance_weight=importance)
        decision_id = f"D{remembered + 1}-{commit.sha[:8]}"
        registry.add_entry(
            decision_id=decision_id,
            data_id=res["data_id"],
            sha=commit.sha,
            decision=decision["decision"],
            rationale=decision["rationale"],
            scope=decision["scope"],
            importance=importance,
        )
        registry.append_event("remember", decision_id=decision_id, sha=commit.sha,
                              decision=decision["decision"], data_id=res["data_id"])
        if res["data_id"]:
            console.print(f"  [blue]data_id:[/blue] {res['data_id']}")
        else:
            console.print(f"  [red]!! no data_id captured — raw: {res['raw'][:120]}[/red]")
        console.print(f"  [blue]registry id:[/blue] {decision_id}\n")
        remembered += 1

    console.print(Panel.fit(
        f"Remembered {remembered} decisions into dataset [cyan]{DATASET_NAME}[/cyan].\n"
        f"Registry: {registry.REGISTRY_PATH}",
        title="Ingest complete",
        style="green",
    ))

    # Checkpoint: recall the apiClient decision
    console.print("\n[bold]Checkpoint — recall('why do we use apiClient'):[/bold]")
    answers = await cognee_client.recall_decisions("why do we use apiClient for HTTP calls")
    for a in answers:
        console.print(f"  - {a[:200]}")

    await cognee_client.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(DEMO_REPO))
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    repo = args.repo if os.path.isabs(args.repo) else os.path.join(os.getcwd(), args.repo)
    if not os.path.isdir(os.path.join(repo, ".git")):
        sys.exit(f"Not a git repo: {repo}  (run scripts/seed_demo_repo.sh first)")
    asyncio.run(ingest(repo, reset=args.reset))


if __name__ == "__main__":
    main()
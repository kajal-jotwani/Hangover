"""Phase 1 — walk demo_repo history, extract durable decisions, remember() them
into Cognee, and record each memory's data_id in the registry.

Usage:
  python ingest.py --repo demo_repo
  python ingest.py --repo demo_repo --reset   # forget the dataset first
  python ingest.py --repo . --since <sha> --head <sha>   # incremental (auto-ingest on merge)
  python ingest.py --repo . --since <sha> --head <sha> --dry-run  # print, don't remember
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
from git_io import log_commits, log_commits_range
from llm import extract_decision

console = Console()


async def ingest(repo_path: str, *, reset: bool, since: str | None = None,
                 head: str | None = None, dry_run: bool = False,
                 max_count: int | None = None, since_date: str | None = None) -> None:
    check_keys(need_cognee=True, need_llm=True)
    # Dry-run still needs the LLM (to extract decisions) but NOT Cognee.
    if not dry_run:
        await cognee_client.connect()

    if reset:
        # Surgical reset: forget each known data_id from the registry, NOT the
        # whole dataset. cognee.forget(dataset=...) can corrupt the dataset on
        # the cloud tenant (subsequent remember() 409s); per-item forget is safe.
        existing = registry.load_registry()
        data_ids = [v.get("data_id") for v in existing.values()
                    if isinstance(v, dict) and v.get("data_id")]
        if data_ids:
            console.print(f"[yellow]Surgical reset: forgetting {len(data_ids)} known memories...[/yellow]")
            res = await cognee_client.forget_many(data_ids)
            console.print(f"  [dim]forgot {res['ok']} ok, {res['failed']} failed[/dim]")
            if res["errors"]:
                console.print(f"  [dim]errors: {res['errors'][:3]}[/dim]")
        else:
            console.print("[dim]Reset: registry empty, starting fresh.[/dim]")
        registry.save_registry({})
        cognee_client.seed_seen(set())  # clear seen-set for clean diffing

    # Incremental range (auto-ingest on merge) vs full history walk.
    if since and head:
        commits = log_commits_range(repo_path, base=since, head=head)
        console.print(f"[bold]Incremental[/bold] {since[:8]}..{head[:8]}: "
                      f"{len(commits)} new commit(s) in [cyan]{repo_path}[/cyan]\n")
    else:
        commits = log_commits(repo_path, max_count=max_count, since=since_date)
        console.print(f"Found {len(commits)} commits in [cyan]{repo_path}[/cyan]\n")

    if dry_run:
        console.print("[yellow]DRY RUN — extracting decisions only, NOT calling remember().[/yellow]\n")

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
        if dry_run:
            console.print(f"  [yellow](dry-run — would remember() with importance {importance:.2f})[/yellow]\n")
            remembered += 1
            continue
        res = await cognee_client.remember_decision(text, importance_weight=importance)
        decision_id = f"D{remembered + 1}-{commit.sha[:8]}"
        registry.add_entry(
            decision_id=decision_id,
            data_id=res["data_id"],
            sha=commit.sha,
            commit_date=commit.date,
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

    if dry_run:
        console.print(Panel.fit(
            f"Dry run: would remember {remembered} decision(s) from "
            f"{len(commits)} commit(s). No memory changed.",
            title="Dry run complete", style="yellow",
        ))
    else:
        console.print(Panel.fit(
            f"Remembered {remembered} decisions into dataset [cyan]{DATASET_NAME}[/cyan].\n"
            f"Registry: {registry.REGISTRY_PATH}",
            title="Ingest complete",
            style="green",
        ))

    # Checkpoint: contrastive recall — the 'before' answer the demo compares against.
    # Only meaningful for the full demo ingest (not incremental / not dry-run).
    if not dry_run and not (since and head):
        console.print("\n[bold]Checkpoint — recall('can I use an in-memory Map cache instead of Redis?'):[/bold]")
        answers = await cognee_client.recall_decisions("can I use an in-memory Map cache instead of Redis?")
        for a in answers:
            console.print(f"  - {a[:220]}")

    if not dry_run:
        await cognee_client.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(DEMO_REPO))
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--since", default=None,
                    help="base SHA for incremental ingest (auto-ingest on merge); "
                         "paired with --head, walks only base..head")
    ap.add_argument("--head", default=None,
                    help="head SHA for incremental ingest; paired with --since")
    ap.add_argument("--dry-run", action="store_true",
                    help="extract decisions and print them, but do NOT call remember() "
                         "(no Cognee connection, no memory change)")
    ap.add_argument("--depth", type=int, default=None,
                    help="limit ingest to the newest N commits")
    ap.add_argument("--since-date", default=None,
                    help="only ingest commits after this date (git log --since format)")
    args = ap.parse_args()
    repo = args.repo if os.path.isabs(args.repo) else os.path.join(os.getcwd(), args.repo)
    if not os.path.isdir(os.path.join(repo, ".git")):
        sys.exit(f"Not a git repo: {repo}  (run scripts/seed_demo_repo.sh first)")
    asyncio.run(ingest(repo, reset=args.reset, since=args.since,
                       head=args.head, dry_run=args.dry_run,
                       max_count=args.depth, since_date=args.since_date))


if __name__ == "__main__":
    main()
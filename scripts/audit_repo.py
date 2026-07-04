#!/usr/bin/env python3
"""CodeMind real-repo audit — the "it works on real code" proof.

Ingests a real OSS repo's commit history into a Cognee Cloud dataset, then
scans the repo's own commits for contradictions against the graph of decisions
CodeMind extracted from that history. Writes CAUGHT.md — real decisions
extracted from real commits, real contradictions caught by the same
detect_core() that runs in CI on every PR. No hand-seeded data.

The audit is PURELY graph-driven: it backs up + blanks the demo's local
memory_registry.json for the run (restores it in finally), so a catch comes
from the Cognee graph, not a hand-seeded local registry. That's the honest
framing for a real repo with no prior CodeMind state — and it's the same
retrieval path the cross-repo proof exercises.

TENANT-GLOBAL CAVEAT (see memory: cognee-cloud-gotchas): the Cognee Cloud
graph is tenant-global, so retrieval can surface decisions from OTHER
datasets on the tenant (e.g. the demo's Redis decision). The audit's queries
are subsystem-scoped to the target repo's paths, which limits cross-pollination,
but for the crispest result run on a clean tenant (scripts/reset_demo_graph.sh
first) and eyeball catches for demo_repo-scoped citations.

Usage:
  python scripts/audit_repo.py --repo /tmp/cognee_real --dataset codemind_cognee \\
      --max-count 40 --scan 25 --out CAUGHT.md
  python scripts/audit_repo.py --cleanup --dataset codemind_cognee
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Run as `python scripts/audit_repo.py` — put the project root on the path so
# the sibling modules (cognee_client, config, git_io, llm, contradiction,
# registry) resolve.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.panel import Panel

import cognee_client
import registry
from config import REGISTRY_PATH, check_keys
from git_io import Commit, log_commits
from llm import extract_decision
from contradiction import detect_core

console = Console()


def _ids_file(dataset: str) -> Path:
    return ROOT / f"{dataset}_data_ids.json"


def _backup_registry() -> Path | None:
    """Stash the demo's memory_registry.json so the audit runs graph-only,
    then restore it when the audit exits (see finally in run())."""
    if REGISTRY_PATH.exists():
        backup = REGISTRY_PATH.with_suffix(".audit_bak.json")
        backup.write_text(REGISTRY_PATH.read_text())
        REGISTRY_PATH.write_text("{}")
        return backup
    return None


def _restore_registry(backup: Path | None) -> None:
    if backup and backup.exists():
        REGISTRY_PATH.write_text(backup.read_text())
        backup.unlink(missing_ok=True)


async def ingest_repo(repo: str, *, commits: list[Commit], dataset: str) -> list[str]:
    """Ingest the given commits' durable decisions into `dataset`.

    Reusable by cross_repo_proof.py. Forgets any previously-captured data_ids
    for this dataset first (surgical, per-item — never forget(dataset=...) which
    corrupts on cloud), so the dataset starts empty and remember()'s seen-set
    isolation works from a clean slate. Returns the list of new data_ids.

    Caller chooses WHICH commits to ingest. The fair-split audit (run()) passes
    the OLDER half of the window here, then scans the NEWER half against the
    resulting graph — so a scanned commit is never judged against a decision
    established by a LATER commit (temporal leakage that would invent false
    contradictions).
    """
    cognee_client.DATASET_NAME = dataset
    ids_file = _ids_file(dataset)
    prior = json.loads(ids_file.read_text()) if ids_file.exists() else []
    if prior:
        console.print(f"[yellow]Cleaning {len(prior)} prior data_id(s) for "
                      f"dataset {dataset}...[/yellow]")
        res = await cognee_client.forget_many(prior)
        console.print(f"  [dim]forgot {res['ok']} ok, {res['failed']} failed[/dim]")
    cognee_client.seed_seen(set())  # dataset is now empty -> seen starts clean
    ids_file.write_text("[]")

    console.print(f"[bold]Ingesting {len(commits)} commits from {repo} into "
                  f"dataset [cyan]{dataset}[/cyan][/bold]\n")

    ids: list[str] = []
    remembered = 0
    for i, c in enumerate(commits, 1):
        console.print(f"[{i}/{len(commits)}] {c.sha[:8]} "
                      f"{c.message.splitlines()[0][:65]}")
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
            ids_file.write_text(json.dumps(ids, indent=2))  # checkpoint per item
        remembered += 1
        console.print(f"  [green]remembered[/green]: {d['decision'][:72]}")
    console.print(Panel.fit(
        f"Remembered {remembered} decisions into {dataset}.\n"
        f"Captured {len(ids)} data_id(s) -> {ids_file.name}",
        title="Ingest complete", style="green"))
    return ids


def _repo_label(repo: str) -> str:
    """Best-effort 'owner/name' label for CAUGHT.md headers."""
    try:
        import subprocess
        url = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=repo, capture_output=True, text=True, check=False,
        ).stdout.strip()
        # ssh: git@github.com:owner/name.git ; https: https://github.com/owner/name.git
        for sep in (":", "/"):
            if sep in url:
                tail = url.rsplit(sep, 2)[-2:]
                name = tail[-1].removesuffix(".git")
                owner = tail[-2] if len(tail) >= 2 else ""
                return f"{owner}/{name}" if owner else name
    except Exception:
        pass
    return Path(repo).name


async def scan_for_contradictions(repo: str, *, commits: list[Commit]) -> list[dict]:
    """Run detect_core over each commit (most-recent first), return the catches.

    A catch = a commit whose diff the judge ruled contradicts a decision in the
    graph. Runs graph-only (caller has blanked the local registry). Each verdict
    carries the retrieval counts so CAUGHT.md can show "local signals: 0".
    """
    catches: list[dict] = []
    for i, c in enumerate(commits, 1):
        console.print(Panel.fit(
            f"[{i}/{len(commits)}] {c.sha[:8]} {c.message.splitlines()[0][:60]}",
            style="bold magenta"))
        try:
            v = await detect_core(repo, branch=None, head=c.sha)
        except Exception as e:
            console.print(f"  [red]detect_core failed: {e}[/red]")
            continue
        if v.get("conflict"):
            v["commit_sha"] = c.sha
            v["subject"] = c.message.splitlines()[0]
            catches.append(v)
            console.print(f"  [red bold]CATCH[/red bold]: {v['decision_violated'][:80]}")
        else:
            console.print("  [green]clean[/green]")
    return catches


def write_caught_md(repo: str, dataset: str, *, ingested: int, remembered: int,
                    scanned: int, catches: list[dict], out: Path) -> None:
    label = _repo_label(repo)
    lines = [
        f"## {label} (dataset `{dataset}`)",
        "",
        f"Ingested {ingested} commits -> {remembered} decisions remembered into the "
        f"Cognee graph. Scanned {scanned} commits -> **{len(catches)} catch(es)**.",
        "",
    ]
    for i, c in enumerate(catches, 1):
        cited = (c.get("graph_nodes") or [""])[0][:280].replace("\n", " ").strip()
        lines += [
            f"### Catch {i} - `{c['commit_sha'][:8]}` {c.get('subject','')}",
            f"- **decision violated:** {c.get('decision_violated','')}",
            f"- **explanation:** {c.get('explanation','')}",
            f"- **confidence:** {c.get('confidence')}",
            (f"- **retrieval:** local signals: {c.get('local_count',0)} | "
             f"semantic recall: {c.get('recalled_count',0)} | "
             f"graph nodes: {c.get('graph_count',0)}"),
        ]
        if cited:
            lines += [f"- **cited graph node:**", f"  > {cited}"]
        lines.append("")
    mode = "a" if out.exists() else "w"
    if mode == "w":
        lines = [
            "# CodeMind - Real-Code Catch Reel",
            "",
            "Real contradictions CodeMind caught in real public repos' own commit "
            "history. Each entry is a commit CodeMind flagged as contradicting a "
            "decision it extracted from the same repo's history - caught by the "
            "same `detect_core()` that runs in CI on every PR. No hand-seeded data.",
            "",
            "Retrieval is **graph-only** (the local registry is blanked for each "
            "run), so a catch comes from the Cognee graph - the same path a repo "
            "with no prior CodeMind state would take.",
            "",
            "**Fair temporal split:** the OLDER half of the audited window is "
            "ingested to build the decision graph; only the NEWER half is scanned "
            "against it. So a scanned commit is never judged against a decision a "
            "LATER commit established - no temporal-leakage false positives. This "
            "mirrors the real product: a team's accumulated memory vs. a new commit.",
            "",
            "Precision labeling is in `EVAL.md`.",
            "",
        ] + lines
    with out.open(mode) as f:
        f.write("\n".join(lines) + "\n")


async def run(repo: str, *, dataset: str, max_count: int, scan: int, out: Path) -> None:
    check_keys(need_cognee=True, need_llm=True)
    if scan >= max_count:
        raise SystemExit(
            f"--scan ({scan}) must be < --max-count ({max_count}) so the ingest "
            f"(older) and scan (newer) windows don't overlap — that separation is "
            f"what prevents temporal leakage (judging an older commit against a "
            f"decision a later commit established).")
    backup = _backup_registry()
    try:
        cognee_client.DATASET_NAME = dataset
        await cognee_client.connect()
        try:
            # One log pass, then a fair temporal split: the OLDER (max_count - scan)
            # commits build the decision graph; only the NEWER `scan` commits are
            # scanned against it. So a scanned commit is never judged against a
            # decision established by a later commit — no temporal leakage.
            commits = log_commits(repo, max_count=max_count)  # oldest-first
            ingest_commits = commits[:-scan] if scan > 0 else commits
            scan_commits = commits[-scan:] if scan > 0 else []
            console.print(f"[bold]Window[/bold]: {len(commits)} commits. "
                          f"Fair split -> ingest {len(ingest_commits)} older, "
                          f"scan {len(scan_commits)} newer.\n")
            ids = await ingest_repo(repo, commits=ingest_commits, dataset=dataset)
            console.print(f"\n[bold]Scanning {len(scan_commits)} newer commits "
                          f"for contradictions against the older decisions...[/bold]\n")
            catches = await scan_for_contradictions(repo, commits=scan_commits)
            write_caught_md(repo, dataset,
                            ingested=len(ingest_commits), remembered=len(ids),
                            scanned=len(scan_commits), catches=catches, out=out)
            console.print(Panel.fit(
                f"Ingested {len(ingest_commits)} older commits -> {len(ids)} decisions.\n"
                f"Scanned {len(scan_commits)} newer commits -> {len(catches)} catch(es).\n"
                f"Wrote {out.name}.",
                title="Audit complete", style="green"))
        finally:
            await cognee_client.disconnect()
    finally:
        _restore_registry(backup)


async def cleanup(dataset: str) -> None:
    check_keys(need_cognee=True, need_llm=False)
    cognee_client.DATASET_NAME = dataset
    await cognee_client.connect()
    ids_file = _ids_file(dataset)
    ids = json.loads(ids_file.read_text()) if ids_file.exists() else []
    console.print(f"[yellow]Surgically forgetting {len(ids)} data_id(s) from "
                  f"dataset {dataset}...[/yellow]")
    if ids:
        res = await cognee_client.forget_many(ids)
        console.print(f"  forgot {res['ok']} ok, {res['failed']} failed")
        if res["errors"]:
            console.print(f"  errors: {res['errors'][:3]}")
    else:
        console.print("  [dim]no data_ids recorded - nothing to forget.[/dim]")
    ids_file.unlink(missing_ok=True)
    await cognee_client.disconnect()
    console.print(Panel.fit(f"Cloud dataset {dataset} restored to its pre-audit state.",
                            title="Cleanup complete", style="yellow"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/tmp/cognee_real",
                    help="path to a clone of the repo to audit")
    ap.add_argument("--dataset", default="codemind_cognee",
                    help="Cognee Cloud dataset to ingest into + scan against")
    ap.add_argument("--max-count", type=int, default=40,
                    help="number of recent commits to ingest as decision memory")
    ap.add_argument("--scan", type=int, default=25,
                    help="number of most-recent commits to scan as candidate violations")
    ap.add_argument("--out", default=str(ROOT / "CAUGHT.md"),
                    help="CAUGHT.md path (appends per repo)")
    ap.add_argument("--cleanup", action="store_true",
                    help="forget every data_id for the dataset + exit")
    args = ap.parse_args()
    if args.cleanup:
        asyncio.run(cleanup(args.dataset))
    else:
        asyncio.run(run(args.repo, dataset=args.dataset, max_count=args.max_count,
                        scan=args.scan, out=Path(args.out)))


if __name__ == "__main__":
    main()
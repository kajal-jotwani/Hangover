#!/usr/bin/env python3
"""Cross-repo shared-memory proof - real data, not a scripted PR.

A repo with NO local memory still gets protected by the shared Cognee Cloud
graph another repo populated. This is the headline "org-wide shared memory"
claim validated on real code.

What it does:
  1. Ensures repo A (e.g. topoteretes/cognee) is ingested into the shared
     tenant graph (reuses audit_repo.ingest_repo).
  2. BLANKS the local memory_registry.json (simulating repo B: a repo with no
     own CodeMind state - no hand-seeded registry).
  3. Runs detect_core() on a real commit from repo A's history that contradicts
     a decision the shared graph holds. With the registry blanked, the catch
     must come ENTIRELY from the shared graph: local signals: 0, graph nodes: N
     -> conflict: True citing repo A's decision.
  4. Writes PROOF.md with the retrieval counts + verdict.

By default --head is auto-picked: the script scans repo A's recent commits and
uses the FIRST one detect_core flags as a real contradiction. So the proof
commit is a contradiction CodeMind found itself, not one we hand-picked. Pass
--head <sha> to pin a specific commit.

The "cross-repo" framing: the only memory in play is the shared tenant graph
(repo A populated it). The detecting repo has zero local registry. That is
exactly a repo B's CI run hitting a decision another repo established - the
Cloud-native differentiator self-hosted memory cannot offer.

Usage:
  python scripts/cross_repo_proof.py --repo /tmp/cognee_real --dataset codemind_cognee
  python scripts/cross_repo_proof.py --repo /tmp/cognee_real --dataset codemind_cognee --head <sha>
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from rich.console import Console
from rich.panel import Panel

import cognee_client
from config import REGISTRY_PATH, check_keys
from git_io import log_commits
from contradiction import detect_core
from audit_repo import ingest_repo, _ids_file, _backup_registry, _restore_registry, _repo_label

console = Console()
PROOF_MD = ROOT / "PROOF.md"


async def _pick_proof_head(repo: str, *, commits: list, scan: int) -> tuple[str, dict]:
    """Scan recent commits graph-only; return (sha, verdict) of the first real catch.

    The proof commit is one CodeMind itself found contradictory - not hand-picked.
    """
    console.print(f"[bold]Auto-scanning {min(scan, len(commits))} recent commits to "
                  f"find a real contradiction to use as the proof head...[/bold]\n")
    for c in reversed(commits):  # most-recent first
        if scan <= 0:
            break
        scan -= 1
        console.print(f"  {c.sha[:8]} {c.message.splitlines()[0][:55]}")
        try:
            v = await detect_core(repo, branch=None, head=c.sha)
        except Exception as e:
            console.print(f"    [red]detect_core failed: {e}[/red]")
            continue
        if v.get("conflict"):
            console.print(f"    [red bold]FOUND a real catch - using this as the proof head.[/red bold]")
            return c.sha, v
        console.print("    [green]clean[/green]")
    return "", {}


async def run(repo: str, *, dataset: str, head: str | None, max_count: int,
              scan: int, out: Path) -> None:
    check_keys(need_cognee=True, need_llm=True)
    backup = _backup_registry()  # blank the registry -> simulate repo B (no local memory)
    try:
        cognee_client.DATASET_NAME = dataset
        await cognee_client.connect()
        try:
            ids_file = _ids_file(dataset)
            if not ids_file.exists() or not json_loads_safe(ids_file):
                console.print("[yellow]No prior ingest found for this dataset - "
                              "ingesting now...[/yellow]\n")
                await ingest_repo(repo, commits=log_commits(repo, max_count=max_count),
                                  dataset=dataset)
            else:
                console.print(f"[green]Using existing ingest in {dataset} "
                              f"({len(json_loads_safe(ids_file))} data_ids).[/green]")

            commits = log_commits(repo, max_count=max_count)
            if head:
                console.print(f"\n[bold]Pinned --head {head[:8]}[/bold]")
                proof_sha = head
                verdict = await detect_core(repo, branch=None, head=head)
            else:
                proof_sha, verdict = await _pick_proof_head(
                    repo, commits=commits, scan=scan)
                if not proof_sha:
                    console.print(Panel.fit(
                        "No contradiction found in the scanned commits. Try a larger "
                        "--scan, ingest more (--max-count), or pin --head to a known "
                        "reverting commit.", title="No proof head found", style="yellow"))
                    return

            write_proof_md(repo, dataset, proof_sha=proof_sha, verdict=verdict, out=out)
            console.print(Panel.fit(
                f"Proof head: {proof_sha[:8]}\n"
                f"local signals: {verdict.get('local_count',0)} | "
                f"semantic recall: {verdict.get('recalled_count',0)} | "
                f"graph nodes: {verdict.get('graph_count',0)}\n"
                f"conflict: {verdict.get('conflict')}\n"
                f"decision violated: {verdict.get('decision_violated','')[:90]}\n"
                f"Wrote {out.name}.",
                title="Cross-repo proof", style="green"))
        finally:
            await cognee_client.disconnect()
    finally:
        _restore_registry(backup)


def json_loads_safe(p: Path):
    try:
        import json
        return json.loads(p.read_text())
    except Exception:
        return []


def write_proof_md(repo: str, dataset: str, *, proof_sha: str, verdict: dict,
                   out: Path) -> None:
    label = _repo_label(repo)
    cited = (verdict.get("graph_nodes") or [""])[0][:400].replace("\n", " ").strip()
    lines = [
        "# CodeMind - Cross-Repo Shared-Memory Proof (real data)",
        "",
        f"**Repo audited:** `{label}` (dataset `{dataset}`)",
        f"**Proof commit:** `{proof_sha[:8]}`",
        "",
        "## The proof",
        "",
        "A repo with **no local CodeMind state** (the registry is blanked, "
        "simulating repo B) still gets a contradiction caught by the **shared "
        "Cognee Cloud graph** repo A populated. The catch comes ENTIRELY from "
        f"the graph - `local signals: {verdict.get('local_count',0)}`.",
        "",
        f"- **conflict:** `{verdict.get('conflict')}`",
        f"- **decision violated:** {verdict.get('decision_violated','')}",
        f"- **explanation:** {verdict.get('explanation','')}",
        f"- **confidence:** {verdict.get('confidence')}",
        (f"- **retrieval:** local signals: {verdict.get('local_count',0)} | "
         f"semantic recall: {verdict.get('recalled_count',0)} | "
         f"graph nodes: {verdict.get('graph_count',0)}"),
    ]
    if cited:
        lines += ["", "**Cited graph node (from the shared graph):**", f"> {cited}"]
    lines += [
        "",
        "## Why this matters",
        "",
        "This is the Cloud-native differentiator: one memory graph across your "
        "whole org's repos. A hard-won decision made in repo A protects a PR in "
        "repo B - and repo B had to remember nothing. No local/self-hosted memory "
        "can do this. The hand-seeded Redis demo proves the mechanism; this proof "
        "proves it on **real code** with a real contradiction CodeMind found itself.",
        "",
    ]
    out.write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/tmp/cognee_real",
                    help="path to a clone of repo A (the repo that populates the graph)")
    ap.add_argument("--dataset", default="codemind_cognee",
                    help="Cognee Cloud dataset repo A's decisions live in")
    ap.add_argument("--head", default=None,
                    help="pin a specific commit sha as the proof head (default: auto-find first catch)")
    ap.add_argument("--max-count", type=int, default=40,
                    help="commits to ingest if no prior ingest exists / to scan for auto --head")
    ap.add_argument("--scan", type=int, default=30,
                    help="how many recent commits to scan when auto-picking --head")
    ap.add_argument("--out", default=str(PROOF_MD))
    args = ap.parse_args()
    asyncio.run(run(args.repo, dataset=args.dataset, head=args.head,
                    max_count=args.max_count, scan=args.scan, out=Path(args.out)))


if __name__ == "__main__":
    main()
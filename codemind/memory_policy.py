from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

POLICY_FILENAME = "codemind_config.json"


def _resolve_root(repo_root: Path | None) -> Path:
    if repo_root is not None:
        return Path(repo_root)
    # Late import to avoid pulling onboarding's deps at module import time.
    from codemind.onboarding import discover_repo_root
    return discover_repo_root()


@dataclass
class MemoryPolicy:
    depth: int | None = None
    since: str | None = None
    scope: str = "private"
    dataset: str | None = None
    auto_ingest: bool = False


def load_policy(repo_root: Path | None = None) -> MemoryPolicy:
    path = _resolve_root(repo_root) / POLICY_FILENAME
    if not path.exists():
        return MemoryPolicy()
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return MemoryPolicy()
    return MemoryPolicy(
        depth=payload.get("depth"),
        since=payload.get("since"),
        scope=payload.get("scope", "private"),
        dataset=payload.get("dataset"),
        auto_ingest=bool(payload.get("auto_ingest", False)),
    )


def save_policy(policy: MemoryPolicy, repo_root: Path | None = None) -> Path:
    path = _resolve_root(repo_root) / POLICY_FILENAME
    path.write_text(json.dumps(asdict(policy), indent=2) + "\n")
    return path


def _remember_ts(event: dict[str, Any]) -> float:
    ts = event.get("ts")
    try:
        return float(ts)
    except Exception:
        return 0.0


def _entry_time(entry: dict[str, Any], events: list[dict[str, Any]]) -> float:
    commit_date = entry.get("commit_date") or ""
    if isinstance(commit_date, str) and commit_date:
        try:
            return datetime.fromisoformat(commit_date.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    decision_id = entry.get("decision_id")
    for event in events:
        if event.get("kind") == "remember" and event.get("decision_id") == decision_id:
            return _remember_ts(event)
    return 0.0


def _active_citations(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        if event.get("kind") != "contradiction":
            continue
        data_id = event.get("data_id") or ""
        if data_id:
            counts[data_id] = counts.get(data_id, 0) + 1
    return counts


def prune_candidates(registry: dict[str, dict[str, Any]], events: list[dict[str, Any]], *,
                     older_than_days: int | None = None,
                     older_than_commits: int | None = None) -> list[dict[str, Any]]:
    active = [(decision_id, entry) for decision_id, entry in registry.items() if entry.get("status") == "active"]
    active.sort(key=lambda item: _entry_time({**item[1], "decision_id": item[0]}, events))
    citation_counts = _active_citations(events)

    candidates: list[dict[str, Any]] = []
    cutoff_ts = None
    if older_than_days is not None:
        cutoff_ts = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    threshold_index = None
    if older_than_commits is not None and older_than_commits >= 0:
        threshold_index = max(0, len(active) - older_than_commits)

    for index, (decision_id, entry) in enumerate(active):
        entry_ts = _entry_time({**entry, "decision_id": decision_id}, events)
        if cutoff_ts is not None and entry_ts and datetime.fromtimestamp(entry_ts, tz=timezone.utc) > cutoff_ts:
            continue
        if threshold_index is not None and index >= threshold_index:
            continue
        if citation_counts.get(entry.get("data_id", ""), 0) > 0:
            continue
        candidates.append({"decision_id": decision_id, "entry": entry, "entry_ts": entry_ts})
    return candidates


def decay_importance(value: float, factor: float = 0.5) -> float:
    return round(max(0.0, value * factor), 4)

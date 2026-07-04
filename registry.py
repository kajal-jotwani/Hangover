"""Local state: the memory registry + the 'belief changed' event log.

memory_registry.json maps a decision_id -> {data_id, sha, decision, rationale,
scope, importance, status}. The data_id is what let us forget a single memory
surgically. This file is the bridge between Cognee's graph and our CLI.

event_log.json is the append-only timeline of remember/improve/forget events —
feeds the (stretch) dashboard and the demo's 'belief changed' visual.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from config import EVENT_LOG_PATH, REGISTRY_PATH


def _words(text: str) -> set[str]:
    """Significant word tokens, punctuation-stripped + lowercased, len > 4.

    Used by the fuzzy match so 'Redis;' (punctuation attached) still matches
    'redis' — reconcile confirm relies on this to find the right data_id to forget.
    """
    return {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_-]+", (text or "").lower()) if len(w) > 4}


def _read(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2))


def load_registry() -> dict[str, dict]:
    return _read(REGISTRY_PATH, {})


def save_registry(reg: dict[str, dict]) -> None:
    _write(REGISTRY_PATH, reg)


def upsert_entry(decision_id: str, **fields) -> None:
    reg = load_registry()
    reg[decision_id] = {**(reg.get(decision_id, {})), **fields}
    save_registry(reg)


def add_entry(*, decision_id: str, data_id: str | None, sha: str,
              decision: str, rationale: str, scope: str, importance: float,
              commit_date: str | None = None) -> None:
    fields = dict(
        data_id=data_id, sha=sha, decision=decision, rationale=rationale,
        scope=scope, importance=importance, status="active",
    )
    if commit_date is not None:
        fields["commit_date"] = commit_date
    upsert_entry(decision_id, **fields)


def find_by_scope(touched_files: list[str]) -> list[dict]:
    """Deterministic scope match — the safety net under semantic recall.

    Returns active registry entries whose scope mentions a path/pattern that
    overlaps the touched files. Used in hybrid retrieval so a recall miss
    can't sink the contradiction demo.
    """
    reg = load_registry()
    out = []
    touched_l = [t.lower() for t in touched_files]
    for entry in reg.values():
        if entry.get("status") != "active":
            continue
        scope = (entry.get("scope") or "").lower()
        if not scope:
            continue
        scope_tokens = [t.strip() for t in scope.replace(",", " ").split() if t.strip()]
        for tok in scope_tokens:
            if any(tok in tf for tf in touched_l):
                out.append(entry)
                break
    return out


def find_by_decision_text(decision_text: str) -> dict | None:
    """Fuzzy-match a judge's 'decision_violated' string back to a registry entry
    so we can find the data_id to forget. Containment both ways."""
    if not decision_text:
        return None
    needle = decision_text.lower().strip()
    reg = load_registry()
    # exact-ish first
    for entry in reg.values():
        if entry.get("status") != "active":
            continue
        if needle and needle in (entry.get("decision") or "").lower():
            return entry
    # loose: shared significant words (punctuation-stripped so 'Redis;' matches 'redis')
    needle_words = _words(needle)
    for entry in reg.values():
        if entry.get("status") != "active":
            continue
        dec_words = _words(entry.get("decision") or "")
        if needle_words and len(needle_words & dec_words) >= 2:
            return entry
    return None


def all_active() -> list[dict]:
    return [e for e in load_registry().values() if e.get("status") == "active"]


def append_event(kind: str, **fields) -> None:
    events = _read(EVENT_LOG_PATH, [])
    events.append({"ts": time.time(), "kind": kind, **fields})
    _write(EVENT_LOG_PATH, events)


def load_events() -> list[dict]:
    return _read(EVENT_LOG_PATH, [])
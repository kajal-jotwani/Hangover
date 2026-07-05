"""Thin async wrapper over the Cognee v1.0 SDK.

Verified signatures (docs.cognee.ai/python-api):
  cognee.serve(url=..., api_key=...)
  cognee.remember(data, dataset_name=..., importance_weight=..., self_improvement=True)
  cognee.recall(query_text=..., datasets=[...], top_k=15, auto_route=True)
  cognee.forget(*, data_id=<UUID>, dataset=..., everything=False, memory_only=False)
  cognee.improve(dataset_name=..., session_ids=[...])
  cognee.disconnect()

The exact return shape of remember() is confirmed in spike.py; here we extract a
per-item id defensively from several possible attribute paths so forget_one() can
target a single memory.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import cognee

from codemind.runtime.config import COGNEE_API_KEY, COGNEE_TENANT_ID, COGNEE_URL, COGNEE_USER_ID, DATASET_NAME

_cloud_client = None

# Track data_ids we've already seen this session, so we can isolate the NEW
# item id from remember()'s full items list (which is NOT in insertion order).
# Seed this from memory_registry.json before remembering into an existing dataset.
_seen_data_ids: set[str] = set()


def seed_seen(ids: set[str]) -> None:
    """Pre-populate the seen-id set with data_ids already in the dataset.

    Call this before remember_decision() when the dataset is non-empty (e.g.
    reconcile.py reads memory_registry.json and seeds the existing ids so the
    new 'superseded' memory's id can be isolated by diffing).
    """
    _seen_data_ids.update(ids)


async def connect(*, url: str | None = None, api_key: str | None = None,
                  tenant_id: str | None = None, user_id: str | None = None) -> None:
    """Route all Cognee ops to the Cloud tenant.

    cognee.serve() returns a CloudClient whose session only carries X-Api-Key.
    If COGNEE_TENANT_ID / COGNEE_USER_ID are set, we patch the client's session
    builder to also send X-Tenant-Id / X-User-Id (harmless if the server
    ignores them, required if your tenant needs them).
    """
    global _cloud_client
    resolved_url = url if url is not None else COGNEE_URL
    resolved_api_key = api_key if api_key is not None else COGNEE_API_KEY
    resolved_tenant_id = tenant_id if tenant_id is not None else COGNEE_TENANT_ID
    resolved_user_id = user_id if user_id is not None else COGNEE_USER_ID
    if not resolved_url or not resolved_api_key:
        raise SystemExit("COGNEE_URL / COGNEE_API_KEY not set in .env")
    _cloud_client = await cognee.serve(url=resolved_url, api_key=resolved_api_key)
    _inject_headers(_cloud_client, tenant_id=resolved_tenant_id, user_id=resolved_user_id)


def _inject_headers(client, *, tenant_id: str | None = None,
                    user_id: str | None = None) -> None:
    """Patch CloudClient._get_session so the aiohttp session includes the
    tenant/user headers alongside X-Api-Key."""
    tenant = tenant_id if tenant_id is not None else COGNEE_TENANT_ID
    user = user_id if user_id is not None else COGNEE_USER_ID
    if not (tenant or user):
        return
    try:
        import aiohttp
    except ImportError:
        return

    async def _get_session_with_tenant():
        if client._session is None or client._session.closed:
            headers = {"X-Api-Key": client.api_key}
            if tenant:
                headers["X-Tenant-Id"] = tenant
            if user:
                headers["X-User-Id"] = user
            client._session = aiohttp.ClientSession(
                headers=headers, timeout=client.DEFAULT_TIMEOUT
            )
        return client._session

    client._get_session = _get_session_with_tenant


async def disconnect() -> None:
    try:
        await cognee.disconnect()
    except Exception:
        pass


def _coerce_id(value: Any) -> str | None:
    """Normalize a UUID-ish value to a string, or None."""
    if value is None:
        return None
    if isinstance(value, (uuid.UUID,)):
        return str(value)
    if isinstance(value, str) and value:
        return value
    return None


def _extract_data_id(result: Any) -> str | None:
    """Defensively pull a per-item data id out of remember()'s return value.

    The spike prints the real shape; this covers the likely attribute names so
    production code works regardless of which one Cognee exposes.
    """
    if result is None:
        return None
    # Direct attributes
    for attr in ("data_id", "id", "data_item_id", "item_id"):
        if hasattr(result, attr):
            got = _coerce_id(getattr(result, attr))
            if got:
                return got
    # Nested .data
    data = getattr(result, "data", None) if not isinstance(result, (str, bytes)) else None
    if data is not None:
        for attr in ("data_id", "id", "data_item_id", "item_id"):
            if hasattr(data, attr):
                got = _coerce_id(getattr(data, attr))
                if got:
                    return got
    # Dict-like
    if isinstance(result, dict):
        # The live cloud SDK returns {"items": [{"id": <uuid>, ...}, ...]} —
        # the per-item data_id is the id of the last inserted item.
        items = result.get("items")
        if isinstance(items, list) and items:
            last = items[-1]
            if isinstance(last, dict):
                got = _coerce_id(last.get("id") or last.get("data_id"))
                if got:
                    return got
        for k in ("data_id", "id", "data_item_id", "item_id"):
            got = _coerce_id(result.get(k))
            if got:
                return got
        data = result.get("data")
        if isinstance(data, dict):
            for k in ("data_id", "id", "data_item_id", "item_id"):
                got = _coerce_id(data.get(k))
                if got:
                    return got
        # Also handle .data being a list of items
        if isinstance(data, list) and data and isinstance(data[-1], dict):
            got = _coerce_id(data[-1].get("id") or data[-1].get("data_id"))
            if got:
                return got
    # Object with .items (list of objects/dicts)
    items = getattr(result, "items", None) if not isinstance(result, (str, bytes)) else None
    if isinstance(items, list) and items:
        last = items[-1]
        if hasattr(last, "id"):
            got = _coerce_id(getattr(last, "id"))
            if got:
                return got
        if isinstance(last, dict):
            got = _coerce_id(last.get("id") or last.get("data_id"))
            if got:
                return got
    return None


def _extract_dataset_id(result: Any) -> str | None:
    for attr in ("dataset_id", "datasetId"):
        if hasattr(result, attr):
            return _coerce_id(getattr(result, attr))
    if isinstance(result, dict):
        return _coerce_id(result.get("dataset_id") or result.get("datasetId"))
    return None


async def remember_decision(text: str, *, importance_weight: float | None = None) -> dict:
    """Store one decision fact. Returns {data_id, dataset_id, raw}.

    importance_weight nudges the graph (supports the 'memory reinforced' beat).

    The new item's data_id is isolated by DIFFING the items list before/after:
    remember() returns the FULL items list (all items in the dataset) which is
    NOT in insertion order, so items[-1] is unreliable. We snapshot the seen set,
    call remember, and the new id = (items_after) - (seen_before). For a non-empty
    dataset the caller must seed_seen() with the existing ids first.
    """
    kwargs: dict[str, Any] = {"dataset_name": DATASET_NAME, "self_improvement": True}
    if importance_weight is not None:
        kwargs["importance_weight"] = importance_weight

    before = set(_seen_data_ids)
    result = await cognee.remember(text, **kwargs)

    # Pull the full items list (cloud mode returns a dict with 'items').
    after_ids: set[str] = set()
    items = []
    if isinstance(result, dict):
        items = result.get("items") or []
        for it in items:
            if isinstance(it, dict):
                gid = _coerce_id(it.get("id") or it.get("data_id"))
                if gid:
                    after_ids.add(gid)

    new_ids = after_ids - before
    if new_ids:
        data_id = new_ids.pop()
    elif items:
        # Fallback: dedup may have merged the new content into an existing node,
        # or seen-set wasn't seeded. Take a best-guess and warn.
        data_id = _coerce_id(items[-1].get("id") or items[-1].get("data_id"))
        print(f"  [warn] could not isolate new data_id; using fallback {data_id}")
    else:
        data_id = _extract_data_id(result)

    _seen_data_ids.update(after_ids)
    return {
        "data_id": data_id,
        "dataset_id": _extract_dataset_id(result),
        "raw": repr(result)[:300],
    }


async def recall_decisions(query: str, *, top_k: int = 15) -> list[str]:
    """Return recalled decision texts.

    In cloud mode (cognee.serve) recall() returns a list of dicts; in local
    mode it returns Pydantic RecallResponse objects. Handle both.
    """
    responses = await cognee.recall(
        query_text=query, datasets=[DATASET_NAME], top_k=top_k, auto_route=True
    )
    out: list[str] = []
    for r in responses or []:
        text = _recall_text(r)
        if text is None:
            text = json.dumps(_to_serializable(r))
        out.append(text)
    return out


def _recall_text(r) -> str | None:
    """Pull human-readable text from a recall result (dict or object)."""
    for attr in ("answer", "text", "content", "response", "summary"):
        v = r.get(attr) if isinstance(r, dict) else getattr(r, attr, None)
        if isinstance(v, str) and v.strip():
            return v
    return None


async def search_graph_nodes(query: str, *, top_k: int = 10,
                              feedback_influence: float = 0.0) -> list[str]:
    """Pull the actual Cognee graph NODES that match the query (not an LLM answer).

    Uses cognee.search(only_context=True) — the deepest retrieval Cognee exposes
    on the cloud tenant (recall() is a thin wrapper around the same engine; this
    returns the raw graph nodes with their content + keyword tags). Used to cite
    *which* past decision node a diff contradicts, and to feed the judge richer
    candidates than the recall answer alone.

    feedback_influence (0.0–1.0) weights retrieval by previously-stored human
    feedback — the confirm/reject signal. On this tenant memify()/improve()
    don't persist feedback, so the knob is wired but its effect is limited; it's
    exposed so the reconcile 'proof' search can lean on it once the backend supports it.

    Returns a list of node-content strings (parsed out of the search_result blob).
    """
    try:
        results = await cognee.search(
            query_text=query, datasets=[DATASET_NAME], top_k=top_k,
            only_context=True, feedback_influence=feedback_influence,
        )
    except Exception:
        return []
    nodes: list[str] = []
    for r in results or []:
        blob = _recall_text(r) if not isinstance(r, dict) else None
        if blob is None and isinstance(r, dict):
            blob = r.get("search_result") or _recall_text(r) or json.dumps(_to_serializable(r))
        if not blob:
            continue
        # The blob looks like:
        #   "Nodes:\nNode: <preview> [kw, kw]\n__node_content_start__\n<full content>..."
        # Pull each node's full content (between __node_content_start__ and the
        # next "Node:" header / end).
        parts = blob.split("__node_content_start__")
        for chunk in parts[1:]:
            content = chunk.split("\nNode:")[0].strip()
            if content:
                nodes.append(content)
        if not nodes and blob.strip().lower().startswith("nodes:"):
            # No content markers — keep the whole blob as one candidate.
            nodes.append(blob.strip())
    return nodes


def _to_serializable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return str(obj)


async def forget_one(data_id: str) -> Any:
    """Surgically delete a single memory by its data_id (requires dataset)."""
    if not data_id:
        raise ValueError("forget_one needs a data_id")
    return await cognee.forget(data_id=data_id, dataset=DATASET_NAME)


async def forget_many(data_ids: list[str]) -> dict:
    """Surgically forget each data_id (best-effort, continues on error).

    Used for --reset. We avoid cognee.forget(dataset=...) because on the cloud
    tenant a dataset-level delete can leave the dataset in a bad server-side
    state (subsequent remember() 409s with a ProgrammingError). Surgical
    per-item forget by data_id is reliable.
    """
    results = {"ok": 0, "failed": 0, "errors": []}
    for did in data_ids:
        if not did:
            continue
        try:
            await cognee.forget(data_id=did, dataset=DATASET_NAME)
            results["ok"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append(str(e)[:120])
    return results


async def forget_dataset() -> Any:
    """Wipe the whole dataset. AVOID on cloud — can corrupt the dataset. Use
    forget_many() with per-item data_ids instead."""
    return await cognee.forget(dataset=DATASET_NAME)


async def improve_graph() -> Any:
    """Re-weight / reconcile the graph after a belief update.

    Best-effort: on the cloud tenant the explicit improve() endpoint 404s, but
    remember(self_improvement=True) already auto-runs improve() — so the graph
    re-weights at the remember() call. We still try the explicit call in case the
    tenant supports it, and swallow the error if not.
    """
    try:
        return await cognee.improve(dataset_name=DATASET_NAME)
    except Exception as e:
        # Non-fatal: self_improvement=True at remember-time already re-weights.
        return f"<improve() best-effort skipped: {e}>"


async def datasets_status() -> Any:
    try:
        return await cognee.datasets.get_status()
    except Exception as e:
        return f"<get_status failed: {e}>"
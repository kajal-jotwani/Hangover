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

from config import COGNEE_API_KEY, COGNEE_TENANT_ID, COGNEE_URL, COGNEE_USER_ID, DATASET_NAME

_cloud_client = None


async def connect() -> None:
    """Route all Cognee ops to the Cloud tenant.

    cognee.serve() returns a CloudClient whose session only carries X-Api-Key.
    If COGNEE_TENANT_ID / COGNEE_USER_ID are set, we patch the client's session
    builder to also send X-Tenant-Id / X-User-Id (harmless if the server
    ignores them, required if your tenant needs them).
    """
    global _cloud_client
    if not COGNEE_URL or not COGNEE_API_KEY:
        raise SystemExit("COGNEE_URL / COGNEE_API_KEY not set in .env")
    _cloud_client = await cognee.serve(url=COGNEE_URL, api_key=COGNEE_API_KEY)
    _inject_headers(_cloud_client)


def _inject_headers(client) -> None:
    """Patch CloudClient._get_session so the aiohttp session includes the
    tenant/user headers alongside X-Api-Key."""
    if not (COGNEE_TENANT_ID or COGNEE_USER_ID):
        return
    try:
        import aiohttp
    except ImportError:
        return

    async def _get_session_with_tenant():
        if client._session is None or client._session.closed:
            headers = {"X-Api-Key": client.api_key}
            if COGNEE_TENANT_ID:
                headers["X-Tenant-Id"] = COGNEE_TENANT_ID
            if COGNEE_USER_ID:
                headers["X-User-Id"] = COGNEE_USER_ID
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
    """
    kwargs: dict[str, Any] = {"dataset_name": DATASET_NAME, "self_improvement": True}
    if importance_weight is not None:
        kwargs["importance_weight"] = importance_weight
    result = await cognee.remember(text, **kwargs)
    return {
        "data_id": _extract_data_id(result),
        "dataset_id": _extract_dataset_id(result),
        "raw": repr(result),
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


async def forget_dataset() -> Any:
    """Wipe the whole dataset (used by reset, not the demo)."""
    return await cognee.forget(dataset=DATASET_NAME)


async def improve_graph() -> Any:
    """Re-weight / reconcile the graph after a belief update."""
    return await cognee.improve(dataset_name=DATASET_NAME)


async def datasets_status() -> Any:
    try:
        return await cognee.datasets.get_status()
    except Exception as e:
        return f"<get_status failed: {e}>"
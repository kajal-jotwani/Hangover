"""Shared configuration: env loading + constants + Cognee connection helper."""
from __future__ import annotations

import subprocess
import os
from pathlib import Path

from dotenv import load_dotenv

def _discover_root() -> Path:
    override = os.getenv("CODEMIND_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
    except Exception:
        pass
    return Path.cwd().resolve()


# Project root = the current repo root when available; otherwise cwd.
ROOT = _discover_root()
load_dotenv(ROOT / ".env")

# --- Cognee Cloud ---
# COGNEE_URL is the tenant API Base URL. COGNEE_API_KEY is sent as X-Api-Key.
# COGNEE_TENANT_ID / COGNEE_USER_ID are injected as X-Tenant-Id / X-User-Id
# headers (only if set) — the SDK only sends X-Api-Key by default, so we patch
# the CloudClient session to add these when your tenant requires them.
COGNEE_URL = os.getenv("COGNEE_URL", "").strip()
COGNEE_API_KEY = os.getenv("COGNEE_API_KEY", "").strip()
COGNEE_TENANT_ID = os.getenv("COGNEE_TENANT_ID", "").strip()
COGNEE_USER_ID = os.getenv("COGNEE_USER_ID", "").strip()
DATASET_NAME = os.getenv("COGNEE_DATASET", "codemind_repo_memory")

# --- Ollama Cloud (OpenAI-compatible) ---
# Base URL https://ollama.com/v1 ; auth is Bearer OLLAMA_API_KEY (sent by the SDK).
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "https://ollama.com/v1").strip()
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b").strip()

# --- GitHub (optional PR comments) ---
GH_TOKEN = os.getenv("GH_TOKEN", "").strip()
GH_REPO = os.getenv("GH_REPO", "")
GH_PR_NUMBER = os.getenv("GH_PR_NUMBER", "")

# --- Local state files ---
REGISTRY_PATH = ROOT / "memory_registry.json"
EVENT_LOG_PATH = ROOT / "event_log.json"
PENDING_CONFLICT_PATH = ROOT / "pending_conflict.json"
DEMO_REPO = ROOT / "demo_repo"


def check_keys(*, need_cognee: bool = True, need_llm: bool = False) -> None:
    """Fail fast with a clear message if required config is missing."""
    missing = []
    if need_cognee:
        if not COGNEE_API_KEY:
            missing.append("COGNEE_API_KEY")
        if not COGNEE_URL:
            missing.append("COGNEE_URL (tenant API Base URL)")
    if need_llm and not OLLAMA_API_KEY:
        missing.append("OLLAMA_API_KEY (Ollama Cloud key)")
    if missing:
        raise SystemExit(
            "Missing env vars: " + ", ".join(missing) + ". "
            f"Fill them in {ROOT / '.env'} (see .env.example)."
        )
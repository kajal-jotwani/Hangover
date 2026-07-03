"""Shared configuration: env loading + constants + Cognee connection helper."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = directory containing this file
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# --- Cognee Cloud ---
COGNEE_URL = os.getenv("COGNEE_URL", "")
COGNEE_API_KEY = os.getenv("COGNEE_API_KEY", "")
DATASET_NAME = os.getenv("COGNEE_DATASET", "codemind_repo_memory")

# --- Anthropic (extraction + contradiction judgment) ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

# --- GitHub (optional PR comments) ---
GH_TOKEN = os.getenv("GH_TOKEN", "").strip()
GH_REPO = os.getenv("GH_REPO", "")
GH_PR_NUMBER = os.getenv("GH_PR_NUMBER", "")

# --- Local state files ---
REGISTRY_PATH = ROOT / "memory_registry.json"
EVENT_LOG_PATH = ROOT / "event_log.json"
PENDING_CONFLICT_PATH = ROOT / "pending_conflict.json"
DEMO_REPO = ROOT / "demo_repo"


def check_keys(*, need_cognee: bool = True, need_anthropic: bool = False) -> None:
    """Fail fast with a clear message if required keys are missing."""
    missing = []
    if need_cognee and not COGNEE_API_KEY:
        missing.append("COGNEE_API_KEY (and COGNEE_URL)")
    if need_anthropic and not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        raise SystemExit(
            "Missing env vars: " + ", ".join(missing) + ". "
            "Copy .env.example to .env and fill them in."
        )
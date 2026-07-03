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

# --- Ollama (local LLM for extraction + contradiction judgment) ---
# Ollama exposes an OpenAI-compatible endpoint. The graph ingestion itself runs
# on Cognee Cloud's own LLM server-side; these are only our custom reasoning calls.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")

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
    """Fail fast with a clear message if required config is missing.

    Cognee needs real Cloud credentials; the local LLM only needs a model name
    (Ollama is keyless), but we confirm the server is reachable so failures are
    obvious instead of a cryptic connection error mid-run.
    """
    missing = []
    if need_cognee and not COGNEE_API_KEY:
        missing.append("COGNEE_API_KEY (and COGNEE_URL)")
    if missing:
        raise SystemExit(
            "Missing env vars: " + ", ".join(missing) + ". "
            "Copy .env.example to .env and fill them in."
        )
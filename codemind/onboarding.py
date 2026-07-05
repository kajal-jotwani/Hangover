from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from openai import OpenAI

WORKFLOW_TEMPLATE_NAMES = [
    "codemind-pr.yml",
    "codemind-reconcile.yml",
    "codemind-ingest.yml",
]
REQUIRED_ENV_KEYS = [
    "COGNEE_URL",
    "COGNEE_API_KEY",
    "COGNEE_TENANT_ID",
    "COGNEE_USER_ID",
    "OLLAMA_API_KEY",
]
OPTIONAL_ENV_KEYS = ["COGNEE_DATASET", "OLLAMA_MODEL"]
ALL_ENV_KEYS = REQUIRED_ENV_KEYS + OPTIONAL_ENV_KEYS
REPO_VARIABLE_KEY = "CODEMIND_AUTO_INGEST"


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str


def discover_repo_root(start: str | Path | None = None) -> Path:
    base = Path(start or Path.cwd()).resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(base),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
    except Exception:
        pass
    return base


def is_git_repo(path: str | Path) -> bool:
    path = Path(path)
    return (path / ".git").exists()


def read_env_file(env_path: Path) -> dict[str, str]:
    data = dotenv_values(env_path) if env_path.exists() else {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v != ""}


def _canonical_env_lines(values: dict[str, str]) -> list[str]:
    lines = ["# ---- CodeMind environment ----", "# Copy to .env and fill in. Never commit .env.", ""]
    sections = [
        ("# ===== Cognee Cloud =====", ["COGNEE_URL", "COGNEE_API_KEY", "COGNEE_TENANT_ID", "COGNEE_USER_ID", "COGNEE_DATASET"]),
        ("# ===== Ollama Cloud (OpenAI-compatible) =====", ["OLLAMA_BASE_URL", "OLLAMA_API_KEY", "OLLAMA_MODEL"]),
        ("# ===== Optional: real GitHub PR comments =====", ["GH_TOKEN", "GH_REPO", "GH_PR_NUMBER"]),
    ]
    for title, keys in sections:
        lines.append(title)
        for key in keys:
            if key == "OLLAMA_BASE_URL":
                value = values.get(key, "https://ollama.com/v1")
            elif key == "COGNEE_DATASET":
                value = values.get(key, "codemind_repo_memory")
            elif key == "OLLAMA_MODEL":
                value = values.get(key, "gpt-oss:120b")
            else:
                value = values.get(key, "")
            lines.append(f"{key}={value}")
        lines.append("")
    lines.extend([
        "# ===== CI (GitHub Actions) — repo SECRETS, not this file =====",
        "# .github/workflows/codemind-pr.yml + codemind-reconcile.yml read these as secrets:",
        "#   COGNEE_URL, COGNEE_API_KEY, COGNEE_TENANT_ID, COGNEE_USER_ID, OLLAMA_API_KEY",
        "#   (and optional COGNEE_DATASET, OLLAMA_MODEL to override the defaults above)",
        "# GITHUB_TOKEN is auto-provided by Actions (comments post as github-actions[bot]).",
        "# Optional: add a GH_PAT secret to post comments under a human identity instead.",
        "#",
        "# Auto-ingest on merge (codemind-ingest.yml) is OPT-IN. To enable it on a repo, add",
        "# a repository VARIABLE (Settings → Secrets and variables → Actions → Variables):",
        "#   CODEMIND_AUTO_INGEST = true",
        "# (Not a secret — a variable. Left unset on this meta-repo so its own commit",
        "# history never pollutes the demo graph.)",
    ])
    return lines


def merge_env_file(env_path: Path, updates: dict[str, str], *, force: bool = False) -> dict[str, str]:
    existing = read_env_file(env_path)
    merged = dict(existing)
    for key, value in updates.items():
        if value is None:
            continue
        merged[key] = value
    if force or not env_path.exists():
        env_path.write_text("\n".join(_canonical_env_lines(merged)) + "\n")
        return merged

    current_lines = env_path.read_text().splitlines()
    rendered: list[str] = []
    seen: set[str] = set()
    for line in current_lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, old_val = line.partition("=")
            key = key.strip()
            if key in merged:
                rendered.append(f"{key}={merged[key]}")
                seen.add(key)
                continue
        rendered.append(line)
    for key in ALL_ENV_KEYS + ["OLLAMA_BASE_URL", "GH_TOKEN", "GH_REPO", "GH_PR_NUMBER"]:
        if key in merged and key not in seen and all(not line.startswith(f"{key}=") for line in rendered):
            rendered.append(f"{key}={merged[key]}")
    env_path.write_text("\n".join(rendered) + "\n")
    return merged


def workflow_template_text(name: str) -> str:
    return (resources.files("codemind.templates") / name).read_text()


# The codemind CI runtime — sibling modules at the codemind repo root that the
# workflows invoke (contradiction.py, reconcile.py, ingest.py + their imports).
# These are NOT in the codemind package, so init/link vendors them into the
# target repo's .codemind/ so CI is self-contained on a foreign repo.
RUNTIME_FILE_NAMES = [
    "config.py", "cognee_client.py", "contradiction.py", "registry.py",
    "llm.py", "git_io.py", "github.py", "reconcile.py", "ingest.py", "spike.py",
]

CODEMIND_REQUIREMENTS = """\
# codemind CI runtime deps (vendored into .codemind/ by `codemind init`).
cognee>=1.0.0
openai>=1.50.0
pydantic>=2.0
gitpython>=3.1
rich>=13.0
python-dotenv>=1.0
requests>=2.31
"""


def _runtime_source_text(name: str) -> str:
    """Read a runtime module's source from the installed codemind package.

    Works from a wheel/sdist install (via importlib.resources) as well as an
    editable checkout — the runtime modules ship inside ``codemind/runtime/``.
    """
    return (resources.files("codemind.runtime") / name).read_text()


def _rewrite_runtime_imports(text: str) -> str:
    """Rewrite package-relative runtime imports to bare sibling imports.

    The runtime source lives in ``codemind/runtime/`` and imports its siblings
    as ``from codemind.runtime import X`` / ``from codemind.runtime.X import Y``
    so it works as a package submodule. When vendored into a target repo's
    ``.codemind/`` and run as ``python .codemind/contradiction.py`` there is no
    ``codemind`` package on sys.path — only the script's own directory — so those
    imports become ``import X`` / ``from X import Y`` (sibling-relative).
    """
    import re

    # from codemind.runtime import a, b, c  ->  import a, b, c
    text = re.sub(
        r"^(\s*)from codemind\.runtime import (.+)$",
        r"\1import \2",
        text,
        flags=re.MULTILINE,
    )
    # from codemind.runtime.X import a, b  ->  from X import a, b
    text = re.sub(
        r"^(\s*)from codemind\.runtime\.(\w+) import (.+)$",
        r"\1from \2 import \3",
        text,
        flags=re.MULTILINE,
    )
    # import codemind.runtime.X  ->  import X
    text = re.sub(
        r"^(\s*)import codemind\.runtime\.(\w+)(\s+as\s+\w+)?$",
        r"\1import \2\3",
        text,
        flags=re.MULTILINE,
    )
    return text


def vendor_codemind(repo_root: Path, *, force: bool = False) -> list[Path]:
    """Copy the codemind CI runtime source into <repo_root>/.codemind/.

    The workflows run `python .codemind/contradiction.py` / `reconcile.py` /
    `ingest.py`; those import their siblings (config, cognee_client, ...) which
    sit next to them in .codemind/ (Python puts the script's dir on sys.path).
    Source is read from the installed ``codemind.runtime`` package via
    importlib.resources and its package-relative imports are rewritten to bare
    sibling imports so the vendored copies run standalone with no codemind
    package installed.
    """
    target = repo_root / ".codemind"
    target.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in RUNTIME_FILE_NAMES:
        try:
            src_text = _runtime_source_text(name)
        except (FileNotFoundError, ModuleNotFoundError):
            continue
        rewritten = _rewrite_runtime_imports(src_text)
        dst = target / name
        if dst.exists() and not force and dst.read_text() == rewritten:
            continue
        dst.write_text(rewritten)
        copied.append(dst)
    req = target / "codemind-requirements.txt"
    req.write_text(CODEMIND_REQUIREMENTS)
    copied.append(req)
    # Keep the vendored runtime out of the host repo's lint/type scope.
    gitignore = repo_root / ".codemind" / ".gitignore"
    gitignore.write_text("# vendored by `codemind init` — safe to commit\n")
    return copied


def copy_workflows(repo_root: Path, *, include_auto_ingest: bool, force: bool = False) -> list[Path]:
    target_dir = repo_root / ".github" / "workflows"
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in WORKFLOW_TEMPLATE_NAMES:
        if name == "codemind-ingest.yml" and not include_auto_ingest:
            continue
        src_text = workflow_template_text(name)
        dst = target_dir / name
        if dst.exists() and not force:
            if dst.read_text() == src_text:
                copied.append(dst)
            continue
        dst.write_text(src_text)
        copied.append(dst)
    return copied


def gh_available() -> bool:
    try:
        proc = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, check=False)
        return proc.returncode == 0
    except FileNotFoundError:
        return False


def gh_auth_status() -> tuple[bool, str]:
    try:
        proc = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, check=False)
        output = (proc.stdout + proc.stderr).strip()
        return proc.returncode == 0, output
    except FileNotFoundError:
        return False, "gh CLI is not installed"


def gh_repo_exists(repo: str) -> bool:
    try:
        proc = subprocess.run(["gh", "repo", "view", repo], capture_output=True, text=True, check=False)
        return proc.returncode == 0
    except FileNotFoundError:
        return False


def gh_secret_commands(repo: str, values: dict[str, str]) -> list[str]:
    """Return safe-to-echo `gh secret set` commands for the missing secrets.

    The command does NOT embed the secret value — `gh secret set <KEY>` reads it
    from an interactive prompt — so the values never hit terminal scroll, shell
    history, or CI logs. Only keys whose value is present are listed (those are
    the ones `set_repo_secrets` would have pushed).
    """
    commands = []
    for key in REQUIRED_ENV_KEYS + OPTIONAL_ENV_KEYS:
        if values.get(key):
            commands.append(f"gh secret set {key} --repo {repo}   # paste the value when prompted")
    return commands


def set_repo_secrets(repo: str, values: dict[str, str]) -> dict[str, str]:
    result = {"ok": 0, "failed": 0, "skipped": []}
    for key in REQUIRED_ENV_KEYS + OPTIONAL_ENV_KEYS:
        value = values.get(key, "").strip()
        if not value:
            result["skipped"].append(key)
            continue
        try:
            proc = subprocess.run(
                ["gh", "secret", "set", key, "--repo", repo],
                input=value,
                text=True,
                capture_output=True,
                check=False,
            )
            if proc.returncode == 0:
                result["ok"] += 1
            else:
                result["failed"] += 1
        except FileNotFoundError:
            result["failed"] += 1
    return result


def set_repo_variable(repo: str, key: str, value: str) -> bool:
    try:
        proc = subprocess.run(
            ["gh", "variable", "set", key, "--repo", repo],
            input=value,
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.returncode == 0
    except FileNotFoundError:
        return False


async def validate_cognee_credentials(*, url: str, api_key: str, tenant_id: str = "", user_id: str = "") -> ValidationResult:
    from codemind.runtime import cognee_client

    try:
        await cognee_client.connect(url=url, api_key=api_key, tenant_id=tenant_id, user_id=user_id)
        await cognee_client.datasets_status()
        await cognee_client.disconnect()
        return ValidationResult(True, "Cognee connection ok")
    except SystemExit as exc:
        return ValidationResult(False, str(exc))
    except Exception as exc:
        return ValidationResult(False, f"Cognee validation failed: {type(exc).__name__}: {exc}")


async def validate_ollama_credentials(*, base_url: str, api_key: str, model: str) -> ValidationResult:
    try:
        client = OpenAI(base_url=base_url, api_key=api_key)
        models = client.models.list()
        model_ids = []
        if hasattr(models, "data"):
            model_ids = [getattr(m, "id", "") for m in models.data]
        elif isinstance(models, dict):
            model_ids = [m.get("id", "") for m in models.get("data", []) if isinstance(m, dict)]
        if model and model_ids and model not in model_ids:
            return ValidationResult(True, f"Ollama connected, but model '{model}' was not listed")
        return ValidationResult(True, "Ollama connection ok")
    except Exception as exc:
        return ValidationResult(False, f"Ollama validation failed: {type(exc).__name__}: {exc}")


def run_gh_repo_create(repo: str, *, private: bool = True) -> bool:
    cmd = ["gh", "repo", "create", repo]
    if private:
        cmd.append("--private")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return proc.returncode == 0
    except FileNotFoundError:
        return False


def repo_workflow_status(repo_root: Path) -> dict[str, bool]:
    workflow_dir = repo_root / ".github" / "workflows"
    return {name: (workflow_dir / name).exists() for name in WORKFLOW_TEMPLATE_NAMES}

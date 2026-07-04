from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
import webbrowser
from collections import Counter
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codemind.memory_policy import MemoryPolicy, decay_importance, prune_candidates, save_policy
from codemind.onboarding import (
    copy_workflows,
    discover_repo_root,
    gh_auth_status,
    gh_repo_exists,
    gh_secret_commands,
    is_git_repo,
    merge_env_file,
    read_env_file,
    repo_workflow_status,
    run_gh_repo_create,
    set_repo_secrets,
    set_repo_variable,
    validate_cognee_credentials,
    validate_ollama_credentials,
    vendor_codemind,
)

console = Console()


def _mods() -> dict[str, Any]:
    import config
    import cognee_client
    import contradiction
    import ingest as ingest_module
    import registry

    config = importlib.reload(config)
    cognee_client = importlib.reload(cognee_client)
    contradiction = importlib.reload(contradiction)
    ingest_module = importlib.reload(ingest_module)
    registry = importlib.reload(registry)

    return {
        "cognee_client": cognee_client,
        "contradiction": contradiction,
        "ingest_module": ingest_module,
        "registry": registry,
        "ROOT": config.ROOT,
    }


def _repo_root_or_fail() -> Path:
    root = discover_repo_root()
    if not is_git_repo(root):
        raise click.ClickException("This command must be run inside a git repository.")
    return root


def _env_path(root: Path) -> Path:
    return root / ".env"


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def _parse_days(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip().lower()
    if value.endswith("d"):
        value = value[:-1]
    try:
        return int(value)
    except Exception as exc:
        raise click.ClickException(f"Invalid --older-than value: {value!r}") from exc


def _dataset_default(root: Path, scope: str | None) -> str:
    if scope:
        return f"codemind_{root.name}_{scope}"
    return f"codemind_{root.name}"


def _gh_repo_name(root: Path, env: dict[str, str]) -> str:
    if env.get("GH_REPO"):
        return env["GH_REPO"]
    proc = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return root.name


async def _run_initial_ingest(repo_root: Path, policy: MemoryPolicy) -> int:
    mods = _mods()
    registry = mods["registry"]
    ingest_module = mods["ingest_module"]
    before = len(registry.load_registry())
    try:
        await ingest_module.ingest(
            str(repo_root),
            reset=False,
            dry_run=False,
            max_count=policy.depth,
            since_date=policy.since,
        )
    except RuntimeError as exc:
        if "does not have any commits yet" not in str(exc):
            raise
        console.print("[yellow]No commits yet; skipped initial ingest.[/yellow]")
    after = len(registry.load_registry())
    return max(0, after - before)


async def _ingest_flow(repo_root: Path, *, reset: bool, depth: int | None, since: str | None,
                       head: str | None, dry_run: bool) -> None:
    ingest_module = _mods()["ingest_module"]
    if since and head:
        await ingest_module.ingest(str(repo_root), reset=reset, since=since, head=head, dry_run=dry_run, max_count=depth)
    else:
        await ingest_module.ingest(str(repo_root), reset=reset, dry_run=dry_run, max_count=depth, since_date=since)


async def _status_cross_repo() -> dict[str, int]:
    mods = _mods()
    registry = mods["registry"]
    cognee_client = mods["cognee_client"]
    if not registry.load_registry():
        return {"foreign": 0, "total": 0}
    query = "engineering decisions cache redis apiClient logger fetch"
    await cognee_client.connect()
    try:
        recalls = await cognee_client.recall_decisions(query, top_k=10)
        nodes = await cognee_client.search_graph_nodes(query, top_k=10)
    finally:
        await cognee_client.disconnect()
    local_texts = []
    for entry in registry.all_active():
        local_texts.append(entry.get("decision", ""))
        local_texts.append(entry.get("rationale", ""))
    foreign = 0
    for text in list(recalls) + list(nodes):
        if not any(local and local.lower()[:120] in text.lower() for local in local_texts if local):
            foreign += 1
    return {"foreign": foreign, "total": len(recalls) + len(nodes)}


async def _prune_candidates(candidates: list[dict[str, Any]], *, hard: bool, decay_factor: float) -> None:
    mods = _mods()
    registry = mods["registry"]
    cognee_client = mods["cognee_client"]
    if not candidates:
        click.echo("No prune candidates.")
        return

    await cognee_client.connect()
    try:
        if hard:
            data_ids = [c["entry"].get("data_id", "") for c in candidates if c["entry"].get("data_id")]
            result = await cognee_client.forget_many(data_ids)
            click.echo(f"forgot {result['ok']} ok, {result['failed']} failed")
            for candidate in candidates:
                registry.upsert_entry(candidate["decision_id"], status="forgotten")
                registry.append_event(
                    "forget",
                    decision_id=candidate["decision_id"],
                    data_id=candidate["entry"].get("data_id", ""),
                    hard=True,
                )
            return

        for candidate in candidates:
            entry = candidate["entry"]
            new_importance = decay_importance(float(entry.get("importance", 0.5)), decay_factor)
            text = (
                f"Decision: {entry.get('decision', '')}\n"
                f"Rationale: {entry.get('rationale', '')}\n"
                f"Scope: {entry.get('scope', '')}\n"
                f"Source commit: {entry.get('sha', '')}"
            )
            await cognee_client.remember_decision(text, importance_weight=new_importance)
            registry.upsert_entry(candidate["decision_id"], importance=new_importance)
            registry.append_event(
                "decay",
                decision_id=candidate["decision_id"],
                data_id=entry.get("data_id", ""),
                old_importance=entry.get("importance", 0.0),
                new_importance=new_importance,
            )
    finally:
        await cognee_client.disconnect()


async def _forget_one(entry: dict[str, Any]) -> None:
    mods = _mods()
    registry = mods["registry"]
    cognee_client = mods["cognee_client"]
    await cognee_client.connect()
    try:
        await cognee_client.forget_one(entry["data_id"])
        for decision_id, current in registry.load_registry().items():
            if current is entry or current.get("data_id") == entry.get("data_id"):
                registry.upsert_entry(decision_id, status="forgotten")
                registry.append_event("forget", decision_id=decision_id, data_id=entry.get("data_id", ""), manual=True)
                break
    finally:
        await cognee_client.disconnect()


def _write_env_and_policy(repo_root: Path, values: dict[str, str], *, force: bool,
                          dataset: str | None, depth: int | None, since: str | None,
                          scope: str | None, auto_ingest: bool) -> MemoryPolicy:
    env_path = _env_path(repo_root)
    merged = merge_env_file(env_path, values, force=force)
    policy = MemoryPolicy(
        depth=depth,
        since=since,
        scope=scope or merged.get("CODEMIND_SCOPE", "private"),
        dataset=dataset or merged.get("COGNEE_DATASET") or _dataset_default(repo_root, scope),
        auto_ingest=auto_ingest or merged.get("CODEMIND_AUTO_INGEST", "").lower() == "true",
    )
    save_policy(policy, repo_root)
    return policy


async def _init_flow(repo_root: Path, *, yes: bool, values: dict[str, str], force: bool,
                     dataset: str | None, depth: int | None, since: str | None,
                     scope: str | None, no_workflows: bool, no_secrets: bool,
                     auto_ingest: bool) -> None:
    env_path = _env_path(repo_root)
    existing_env = read_env_file(env_path)
    env_values = dict(existing_env)
    env_values.update({k: v for k, v in values.items() if v})

    if not yes:
        if not env_values.get("COGNEE_URL"):
            env_values["COGNEE_URL"] = click.prompt("Cognee tenant URL")
        if not env_values.get("COGNEE_API_KEY"):
            env_values["COGNEE_API_KEY"] = click.prompt("Cognee API key", hide_input=True)
        if not env_values.get("COGNEE_TENANT_ID"):
            env_values["COGNEE_TENANT_ID"] = click.prompt("Cognee tenant id", default="")
        if not env_values.get("COGNEE_USER_ID"):
            env_values["COGNEE_USER_ID"] = click.prompt("Cognee user id", default="")
        if not env_values.get("OLLAMA_API_KEY"):
            env_values["OLLAMA_API_KEY"] = click.prompt("Ollama Cloud API key", hide_input=True)
        if not env_values.get("OLLAMA_MODEL"):
            env_values["OLLAMA_MODEL"] = click.prompt("Ollama model", default="gpt-oss:120b")
        if not env_values.get("OLLAMA_BASE_URL"):
            env_values["OLLAMA_BASE_URL"] = click.prompt("Ollama base URL", default="https://ollama.com/v1")

    required = ["COGNEE_URL", "COGNEE_API_KEY", "OLLAMA_API_KEY"]
    missing = [key for key in required if not env_values.get(key)]
    if missing:
        raise click.ClickException("Missing required init values: " + ", ".join(missing))

    env_values["COGNEE_DATASET"] = dataset or env_values.get("COGNEE_DATASET") or _dataset_default(repo_root, scope)
    env_values["OLLAMA_MODEL"] = env_values.get("OLLAMA_MODEL", "gpt-oss:120b")
    env_values["OLLAMA_BASE_URL"] = env_values.get("OLLAMA_BASE_URL", "https://ollama.com/v1")
    if scope:
        env_values["CODEMIND_SCOPE"] = scope
    if depth is not None:
        env_values["CODEMIND_DEPTH"] = str(depth)
    if since:
        env_values["CODEMIND_SINCE"] = since
    if auto_ingest:
        env_values["CODEMIND_AUTO_INGEST"] = "true"

    cognee = await validate_cognee_credentials(
        url=env_values["COGNEE_URL"],
        api_key=env_values["COGNEE_API_KEY"],
        tenant_id=env_values.get("COGNEE_TENANT_ID", ""),
        user_id=env_values.get("COGNEE_USER_ID", ""),
    )
    if not cognee.ok:
        raise click.ClickException(cognee.message)
    ollama = await validate_ollama_credentials(
        base_url=env_values["OLLAMA_BASE_URL"],
        api_key=env_values["OLLAMA_API_KEY"],
        model=env_values["OLLAMA_MODEL"],
    )
    if not ollama.ok:
        raise click.ClickException(ollama.message)

    policy = _write_env_and_policy(
        repo_root,
        env_values,
        force=force,
        dataset=dataset,
        depth=depth,
        since=since,
        scope=scope,
        auto_ingest=auto_ingest,
    )

    os.environ.update({k: v for k, v in env_values.items() if v is not None})
    _mods()

    copied = []
    vendored = []
    if not no_workflows:
        copied = copy_workflows(repo_root, include_auto_ingest=policy.auto_ingest, force=force)
        vendored = vendor_codemind(repo_root, force=force)

    repo_name = _gh_repo_name(repo_root, env_values)
    secrets_result = {"ok": 0, "failed": 0, "skipped": []}
    gh_ok, _ = gh_auth_status()
    if not no_secrets and gh_ok:
        secrets_result = set_repo_secrets(
            repo_name,
            {
                "COGNEE_URL": env_values.get("COGNEE_URL", ""),
                "COGNEE_API_KEY": env_values.get("COGNEE_API_KEY", ""),
                "COGNEE_TENANT_ID": env_values.get("COGNEE_TENANT_ID", ""),
                "COGNEE_USER_ID": env_values.get("COGNEE_USER_ID", ""),
                "COGNEE_DATASET": env_values.get("COGNEE_DATASET", ""),
                "OLLAMA_API_KEY": env_values.get("OLLAMA_API_KEY", ""),
                "OLLAMA_MODEL": env_values.get("OLLAMA_MODEL", ""),
            },
        )
        if policy.auto_ingest:
            set_repo_variable(repo_name, "CODEMIND_AUTO_INGEST", "true")
    elif not no_secrets:
        click.echo("gh is not available or not authenticated; print these commands instead:")
        for command in gh_secret_commands(repo_name, env_values):
            click.echo(command)
        if policy.auto_ingest:
            click.echo(f"gh variable set CODEMIND_AUTO_INGEST --repo {repo_name}")

    remembered = 0
    if policy.depth is not None or policy.since:
        remembered = await _run_initial_ingest(repo_root, policy)

    console.print(
        Panel.fit(
            f"dataset: [cyan]{env_values.get('COGNEE_DATASET', '')}[/cyan]\n"
            f"remembered: [cyan]{remembered}[/cyan]\n"
            f"workflows: [cyan]{', '.join(str(p.relative_to(repo_root)) for p in copied) if copied else 'skipped'}[/cyan]\n"
            f"vendored: [cyan]{len(vendored)} files in .codemind/[/cyan]\n"
            f"secrets: [cyan]{secrets_result.get('ok', 0)} ok, {secrets_result.get('failed', 0)} failed[/cyan]\n"
            f"registry: [cyan]{'present' if (repo_root / 'memory_registry.json').exists() else 'absent'}[/cyan]\n"
            f"policy: [cyan]{repo_root / 'codemind_config.json'}[/cyan]\n"
            f"next: [cyan]codemind check[/cyan]",
            title="codemind init complete",
            style="green",
        )
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """CodeMind CLI."""


@main.command(name="init")
@click.option("--yes", is_flag=True, help="non-interactive mode; fail if required values are missing")
@click.option("--dataset", default=None, help="Cognee dataset name to use for this repo")
@click.option("--depth", type=int, default=None, help="backfill only the newest N commits")
@click.option("--since", default=None, help="backfill commits after this date")
@click.option("--scope", default=None, help="dataset scope label, e.g. private or shared")
@click.option("--no-workflows", is_flag=True, help="skip writing .github/workflows files")
@click.option("--no-secrets", is_flag=True, help="skip gh secret injection")
@click.option("--auto-ingest", is_flag=True, help="enable codemind-ingest.yml and CODEMIND_AUTO_INGEST")
@click.option("--force", is_flag=True, help="overwrite existing .env/workflows when they differ")
@click.option("--cognee-url", default=None, help="Cognee tenant URL")
@click.option("--cognee-api-key", default=None, help="Cognee API key")
@click.option("--cognee-tenant-id", default=None, help="Cognee tenant id")
@click.option("--cognee-user-id", default=None, help="Cognee user id")
@click.option("--ollama-api-key", default=None, help="Ollama Cloud API key")
@click.option("--ollama-model", default=None, help="Ollama model")
@click.option("--ollama-base-url", default=None, help="Ollama Cloud base URL")
def init(yes: bool, dataset: str | None, depth: int | None, since: str | None, scope: str | None,
         no_workflows: bool, no_secrets: bool, auto_ingest: bool, force: bool,
         cognee_url: str | None, cognee_api_key: str | None, cognee_tenant_id: str | None,
         cognee_user_id: str | None, ollama_api_key: str | None, ollama_model: str | None,
         ollama_base_url: str | None) -> None:
    repo_root = _repo_root_or_fail()
    explicit = {
        "COGNEE_URL": cognee_url,
        "COGNEE_API_KEY": cognee_api_key,
        "COGNEE_TENANT_ID": cognee_tenant_id,
        "COGNEE_USER_ID": cognee_user_id,
        "OLLAMA_API_KEY": ollama_api_key,
        "OLLAMA_MODEL": ollama_model,
        "OLLAMA_BASE_URL": ollama_base_url,
        "COGNEE_DATASET": dataset,
    }
    asyncio.run(_init_flow(repo_root, yes=yes, values=explicit, force=force, dataset=dataset,
                           depth=depth, since=since, scope=scope, no_workflows=no_workflows,
                           no_secrets=no_secrets, auto_ingest=auto_ingest))


@main.command(name="ingest")
@click.option("--repo", default=".", help="repo path to ingest")
@click.option("--reset", is_flag=True, help="surgically forget registered data_ids first")
@click.option("--depth", type=int, default=None, help="only ingest the newest N commits")
@click.option("--since", default=None, help="date filter or base SHA when paired with --head")
@click.option("--head", default=None, help="head SHA for an incremental range ingest")
@click.option("--dry-run", is_flag=True, help="extract decisions but do not remember them")
def ingest(repo: str, reset: bool, depth: int | None, since: str | None, head: str | None, dry_run: bool) -> None:
    repo_root = Path(repo).resolve() if Path(repo).is_absolute() else (discover_repo_root() / repo).resolve()
    asyncio.run(_ingest_flow(repo_root, reset=reset, depth=depth, since=since, head=head, dry_run=dry_run))


@main.group(name="memory")
def memory() -> None:
    """Inspect and manage local memory retention."""


@memory.command(name="status")
def memory_status() -> None:
    """Show registry and event-log health."""
    mods = _mods()
    registry = mods["registry"]
    events = registry.load_events()
    active = registry.all_active()
    table = Table(title="CodeMind memory status")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("active decisions", str(len(active)))
    if active:
        times = [entry.get("commit_date") or entry.get("sha", "") for entry in active]
        table.add_row("oldest", str(min(times)))
        table.add_row("newest", str(max(times)))
    table.add_row("events", str(len(events)))
    counts = Counter((entry.get("scope") or "<unspecified>") for entry in active)
    table.add_row("by scope", ", ".join(f"{scope}:{count}" for scope, count in counts.items()) or "none")
    cited = sum(1 for entry in active if any(ev.get("kind") == "contradiction" and ev.get("data_id") == entry.get("data_id") for ev in events))
    table.add_row("never cited", str(max(0, len(active) - cited)))
    console.print(table)


@memory.command(name="prune")
@click.option("--older-than", default=None, help="decay memories older than this age, e.g. 90d")
@click.option("--older-than-commits", type=int, default=None, help="decay memories older than this rank in the active registry")
@click.option("--hard", is_flag=True, help="forget candidates instead of decaying them")
@click.option("--dry-run", is_flag=True, help="show candidates without mutating memory")
@click.option("--decay-factor", type=float, default=0.5, help="multiply importance by this factor for decay")
def memory_prune(older_than: str | None, older_than_commits: int | None, hard: bool, dry_run: bool, decay_factor: float) -> None:
    """Decay or forget old, uncited memories."""
    mods = _mods()
    registry = mods["registry"]
    candidates = prune_candidates(
        registry.load_registry(),
        registry.load_events(),
        older_than_days=_parse_days(older_than),
        older_than_commits=older_than_commits,
    )
    if dry_run:
        for item in candidates:
            console.print_json(data={
                "decision_id": item["decision_id"],
                "decision": item["entry"].get("decision", ""),
                "data_id": item["entry"].get("data_id", ""),
                "importance": item["entry"].get("importance", 0.0),
            })
        return
    asyncio.run(_prune_candidates(candidates, hard=hard, decay_factor=decay_factor))


@memory.command(name="forget")
@click.argument("decision_text")
def memory_forget(decision_text: str) -> None:
    """Surgically forget one memory by fuzzy decision text."""
    registry = _mods()["registry"]
    entry = registry.find_by_decision_text(decision_text)
    if not entry or not entry.get("data_id"):
        raise click.ClickException("No active memory matched that decision text.")
    if not click.confirm(f"Forget memory '{entry.get('decision', '')}'?", default=False):
        return
    asyncio.run(_forget_one(entry))


@main.command(name="status")
@click.option("--cross-repo", is_flag=True, help="show shared-graph results not present in local registry")
def status(cross_repo: bool) -> None:
    """Health check for env, workflow files, registry, and memory policy."""
    mods = _mods()
    root = mods["ROOT"]
    registry = mods["registry"]
    env_path = _env_path(root)
    table = Table(title="CodeMind status")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row(".env", "present" if env_path.exists() else "missing")
    table.add_row("registry", "present" if (root / "memory_registry.json").exists() else "missing")
    table.add_row("policy", "present" if (root / "codemind_config.json").exists() else "missing")
    table.add_row("workflows", ", ".join(f"{k}:{'yes' if v else 'no'}" for k, v in repo_workflow_status(root).items()))
    table.add_row("active memories", str(len(registry.all_active())))
    table.add_row("dataset", _read_env(env_path).get("COGNEE_DATASET", "codemind_repo_memory"))
    if cross_repo:
        cross = asyncio.run(_status_cross_repo())
        table.add_row("foreign graph signals", f"{cross['foreign']} / {cross['total']}")
    console.print(table)


def _default_branch(root: Path) -> str:
    """Auto-detect the repo's default branch (origin/HEAD); fall back to main."""
    proc = subprocess.run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip().replace("origin/", "", 1)
    return "main"


@main.command(name="check")
@click.option("--base", default=None, help="base ref to diff against (default: repo's default branch)")
def check(base: str | None) -> None:
    """Run contradiction detection against the current branch locally."""
    mods = _mods()
    root = mods["ROOT"]
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, capture_output=True, text=True, check=False).stdout.strip() or "HEAD"
    base_ref = base or _default_branch(root)
    verdict = asyncio.run(mods["contradiction"].detect(str(root), branch=branch, head=None, base=base_ref, post_comment=False))
    console.print_json(data=verdict)


@main.group(name="reconcile")
def reconcile() -> None:
    """Resolve a pending contradiction — confirm (memory revises) or reject (bug caught)."""


@reconcile.command(name="confirm")
@click.option("--reason", required=True, help="why the change is intentional (recorded as the UPDATE rationale)")
@click.option("--query", default=None, help="override the proof recall query")
def reconcile_confirm(reason: str, query: str | None) -> None:
    """Mark the latest conflict intentional: remember UPDATE, forget old, improve, re-ask."""
    import reconcile as reconcile_module
    asyncio.run(reconcile_module._run("confirm", None, reason, query, ci=False))


@reconcile.command(name="reject")
@click.option("--query", default=None, help="override the proof recall query")
def reconcile_reject(query: str | None) -> None:
    """Mark the latest conflict a bug: NO memory change, re-ask to confirm the old belief."""
    import reconcile as reconcile_module
    asyncio.run(reconcile_module._run("reject", None, "", query, ci=False))


@main.command(name="doctor")
@click.option("--cognee", is_flag=True, help="run extra Cognee lifecycle checks")
def doctor(cognee: bool) -> None:
    """Deeper diagnostics for repo state and cloud connectivity."""
    mods = _mods()
    root = mods["ROOT"]
    env = _read_env(_env_path(root))
    lines = [f"git repo: {'yes' if is_git_repo(root) else 'no'}"]
    gh_ok, gh_out = gh_auth_status()
    lines.append(f"gh auth: {'ok' if gh_ok else 'missing'}")
    if gh_out:
        lines.append(gh_out.splitlines()[0])
    lines.append(f"dataset: {env.get('COGNEE_DATASET', 'codemind_repo_memory')}")
    cognee_result = asyncio.run(validate_cognee_credentials(
        url=env.get("COGNEE_URL", ""),
        api_key=env.get("COGNEE_API_KEY", ""),
        tenant_id=env.get("COGNEE_TENANT_ID", ""),
        user_id=env.get("COGNEE_USER_ID", ""),
    ))
    lines.append(f"cognee: {'ok' if cognee_result.ok else 'blocked'}")
    lines.append(cognee_result.message)
    ollama_result = asyncio.run(validate_ollama_credentials(
        base_url=env.get("OLLAMA_BASE_URL", "https://ollama.com/v1"),
        api_key=env.get("OLLAMA_API_KEY", ""),
        model=env.get("OLLAMA_MODEL", "gpt-oss:120b"),
    ))
    lines.append(f"ollama: {'ok' if ollama_result.ok else 'blocked'}")
    lines.append(ollama_result.message)
    if cognee:
        spike = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "spike_lifecycle.py")],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        lines.append("cognee lifecycle spike:")
        lines.extend([line for line in spike.stdout.splitlines() if line.startswith(("===", "OK", "FAIL", "TIMEOUT"))])
    console.print("\n".join(lines))


@main.command(name="dashboard")
@click.option("--open/--no-open", default=True, help="open the generated dashboard in a browser")
def dashboard(open: bool) -> None:
    """Build the dashboard HTML and optionally open it."""
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "dashboard" / "build.py")], check=False)
    if open:
        webbrowser.open((Path(__file__).resolve().parent.parent / "dashboard" / "index.html").as_uri())


@main.command(name="link")
@click.option("--repo", required=True, help="target repo for secrets/workflows")
@click.option("--new", is_flag=True, help="create the repo first if needed")
@click.option("--dataset", default=None, help="dataset name to inject")
@click.option("--no-workflows", is_flag=True, help="skip workflow copy")
@click.option("--no-secrets", is_flag=True, help="skip gh secret injection")
@click.option("--auto-ingest", is_flag=True, help="enable CODEMIND_AUTO_INGEST")
@click.option("--force", is_flag=True, help="overwrite differing workflow files")
def link(repo: str, new: bool, dataset: str | None, no_workflows: bool, no_secrets: bool,
         auto_ingest: bool, force: bool) -> None:
    """Push CodeMind setup to another repo with the same dataset."""
    root = _repo_root_or_fail()
    if new and not gh_repo_exists(repo):
        if not run_gh_repo_create(repo, private=True):
            raise click.ClickException(f"Failed to create repo {repo}")
    env = read_env_file(_env_path(root))
    if dataset:
        env["COGNEE_DATASET"] = dataset
    if not no_workflows:
        copy_workflows(root, include_auto_ingest=auto_ingest or env.get("CODEMIND_AUTO_INGEST", "").lower() == "true", force=force)
        vendor_codemind(root, force=force)
    if not no_secrets:
        result = set_repo_secrets(repo, env)
        console.print(f"secrets: {result['ok']} ok, {result['failed']} failed")
    if auto_ingest:
        set_repo_variable(repo, "CODEMIND_AUTO_INGEST", "true")


if __name__ == "__main__":
    main()

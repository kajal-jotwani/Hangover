"""Read commits and diffs from a git repo via the git CLI."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


def _run(args: list[str], cwd: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout


@dataclass
class Commit:
    sha: str
    date: str
    message: str
    diff: str
    touched_files: list[str]


def log_commits(repo_path: str, *, max_count: int | None = 100,
                since: str | None = None) -> list[Commit]:
    """Walk history with patches. Returns oldest-first.

    `since` is passed through to `git log --since` so callers can bound the walk
    by date without slicing the result after the fact.
    """
    args = ["log", "--pretty=CommitStart%n%H%n%cI%n%s%n%b%nCommitEnd", "-p"]
    if max_count is not None:
        args.insert(2, f"-{max_count}")
    if since:
        args.extend(["--since", since])
    raw = _run(args, repo_path)
    return _parse_log(raw)


def log_commits_range(repo_path: str, base: str, head: str) -> list[Commit]:
    """Walk the commit range base..head with patches. Returns oldest-first.

    Used by the auto-ingest-on-merge workflow: CI passes the push's `before`
    SHA as base and `after` SHA as head, so only the newly-merged commits are
    walked and remembered — incremental, no re-ingesting of history.
    """
    raw = _run(
        ["log", f"{base}..{head}",
         "--pretty=CommitStart%n%H%n%s%n%b%nCommitEnd", "-p"],
        repo_path,
    )
    return _parse_log(raw)


def _parse_log(raw: str) -> list[Commit]:
    commits: list[Commit] = []
    blocks = raw.split("CommitStart\n")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        header, _, rest = block.partition("\nCommitEnd\n")
        if not rest:
            # commit with empty diff
            header = block.replace("\nCommitEnd", "")
            rest = ""
        lines = header.split("\n", 3)
        sha = lines[0].strip()
        date = lines[1].strip() if len(lines) > 1 else ""
        subject = lines[2].strip() if len(lines) > 2 else ""
        body = lines[3].strip() if len(lines) > 3 else ""
        message = (subject + "\n\n" + body).strip()
        touched = _touched_files_from_diff(rest)
        commits.append(Commit(sha=sha, date=date, message=message, diff=rest, touched_files=touched))
    commits.reverse()  # oldest-first
    return commits


def _touched_files_from_diff(diff: str) -> list[str]:
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            # "diff --git a/src/x b/src/x"
            parts = line.split(" b/", 1)
            if len(parts) == 2:
                files.append(parts[1].strip())
    return files


def diff_of_branch(repo_path: str, base: str = "HEAD~1", head: str = "HEAD") -> tuple[str, list[str]]:
    """Return (diff_text, touched_files) for base..head."""
    raw = _run(["diff", f"{base}..{head}"], repo_path)
    return raw, _touched_files_from_diff(raw)


def diff_of_commit(repo_path: str, sha: str) -> tuple[str, list[str]]:
    raw = _run(["show", "--format=", sha], repo_path)
    return raw, _touched_files_from_diff(raw)


def current_head_subject(repo_path: str) -> str:
    return _run(["log", "-1", "--pretty=%s"], repo_path).strip()
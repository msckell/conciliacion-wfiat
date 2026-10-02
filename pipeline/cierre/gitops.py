"""Commit and push data files, for runs that publish (`--push`).

The git identity comes from the environment (the workflow sets it). Each push first
rebases on the remote, because the daily monitor and a close can both write data.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from cierre import REPO_ROOT


class GitError(Exception):
    pass


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def commit_and_push(paths: list[Path], message: str, attempts: int = 3) -> str | None:
    """Commit `paths` and push to origin. Returns the new commit, or None when nothing
    changed."""
    _git("add", "--", *map(_rel, paths))
    if _git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return None
    _git("commit", "-m", message)
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    for i in range(attempts):
        _git("pull", "--rebase", "--autostash", "origin", branch)
        if _git("push", "origin", f"HEAD:{branch}", check=False).returncode == 0:
            return _git("rev-parse", "HEAD").stdout.strip()
        time.sleep(5 * (i + 1))
    raise GitError(f"push to {branch} failed after {attempts} attempts")


def is_tracked(path: Path) -> bool:
    return _git("ls-files", "--error-unmatch", _rel(path), check=False).returncode == 0


def restore(paths: list[Path]) -> None:
    """Put tracked files back to their committed version."""
    tracked = [_rel(p) for p in paths if is_tracked(p)]
    if tracked:
        _git("checkout", "HEAD", "--", *tracked)

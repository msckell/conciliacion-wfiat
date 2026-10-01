"""One structured call to Claude, through Claude Code in headless mode.

Runs `claude -p` with a replaced system prompt, no tools, no settings and a JSON schema,
so it uses the logged in Claude subscription (or CLAUDE_CODE_OAUTH_TOKEN in CI) instead
of API billing. The model comes from ANTHROPIC_MODEL.

Tests pass a fake `Ask` instead, so nothing here runs offline.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_MODEL = "claude-sonnet-5-5"
# Used once when the main model's safeguards refuse a prompt. Seen on 2026-10-01 with
# prompts that only read public transactions (false positives).
DEFAULT_FALLBACK_MODEL = "claude-opus-5-5"


class LlmError(Exception):
    pass


@dataclass
class Reply:
    data: dict
    model: str
    duration_s: float
    cost_usd: float | None


Ask = Callable[[str, str, dict], Reply]


def model_name() -> str:
    return os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL


def ask_claude(system: str, prompt: str, schema: dict, timeout: int = 300) -> Reply:
    try:
        return _ask(system, prompt, schema, model_name(), timeout)
    except LlmError as exc:
        if "safeguards" not in str(exc):
            raise
        fallback = os.environ.get("ANTHROPIC_FALLBACK_MODEL") or DEFAULT_FALLBACK_MODEL
        return _ask(system, prompt, schema, fallback, timeout)


def _ask(system: str, prompt: str, schema: dict, model: str, timeout: int) -> Reply:
    exe = shutil.which("claude")
    if exe is None:
        raise LlmError("claude CLI not found on PATH")
    cmd = [
        exe,
        "-p",
        "--system-prompt",
        system,
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--tools",
        "",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--model",
        model,
        "--json-schema",
        json.dumps(schema),
    ]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise LlmError(f"claude timed out after {timeout}s") from exc
    if proc.returncode != 0 and not proc.stdout:
        raise LlmError(f"claude exit {proc.returncode}: {proc.stderr[:300]}")
    try:
        out = json.loads(proc.stdout)
    except ValueError as exc:
        raise LlmError(f"claude returned non JSON: {proc.stdout[:300]!r}") from exc
    if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
        raise LlmError(f"claude error: {str(out.get('result'))[:300]}")
    return Reply(
        data=out["structured_output"],
        model=model,
        duration_s=round(time.monotonic() - start, 1),
        cost_usd=out.get("total_cost_usd"),
    )

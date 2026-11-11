"""One JSON-in, JSON-out LLM call, through the Claude Code CLI or the Anthropic API, with a disk cache.

Backends (READOUT_LLM=auto|cli|api|none, default auto):
  api   ANTHROPIC_API_KEY is set: the official `anthropic` SDK is used. Nothing is hardcoded.
  cli   the `claude` command line is installed and signed in: a headless call with no tools, no MCP
        servers, no slash commands and no saved settings, run from an empty temporary folder, so the
        trial text it reads cannot make it do anything but return JSON.
  none  no model: the steps that need one fall back to their non-LLM baselines.
Every answer is cached by a hash of the request, so re-running the pipeline makes no new calls.
A hard cap (READOUT_LLM_MAX_CALLS, default 120) stops runaway spending.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import config

MODEL = os.environ.get("READOUT_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("READOUT_EFFORT", "low")
TIMEOUT_S = 180
MAX_CALLS = int(os.environ.get("READOUT_LLM_MAX_CALLS", "120"))
CACHE_DIR = config.CACHE / "llm"
USAGE_FILE = config.RESULTS / "llm_usage.json"

_lock = threading.Lock()
_stopped: list[str] = []  # set once a usage limit is hit, so no further calls are attempted
LIMIT_WORDS = ("usage limit", "rate limit", "limit reached", "quota", "too many requests", "overloaded")


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    pass


def claude_path() -> str | None:
    return shutil.which("claude") or next((str(p) for p in Path.home().glob(".nvm/versions/node/*/bin/claude")), None)


def backend() -> str:
    choice = os.environ.get("READOUT_LLM", "auto")
    if choice != "auto":
        return choice
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if claude_path():
        return "cli"
    return "none"


def _usage() -> dict:
    if USAGE_FILE.exists():
        return json.loads(USAGE_FILE.read_text())
    return {"calls": 0, "cache_hits": 0, "by_task": {}, "models": {}, "cost_usd_reported": 0.0, "seconds": 0.0}


def _record(task: str, model: str | None, seconds: float, cost: float | None, cached: bool) -> None:
    with _lock:
        u = _usage()
        if cached:
            u["cache_hits"] += 1
        else:
            u["calls"] += 1
            u["by_task"][task] = u["by_task"].get(task, 0) + 1
            if model:
                u["models"][model] = u["models"].get(model, 0) + 1
            u["cost_usd_reported"] = round(u["cost_usd_reported"] + (cost or 0.0), 4)
            u["seconds"] = round(u["seconds"] + seconds, 1)
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        USAGE_FILE.write_text(json.dumps(u, indent=2) + "\n")


def calls_made() -> int:
    return _usage()["calls"]


def complete_json(system: str, message: str, schema: dict, task: str) -> dict:
    """Return the model's JSON answer (shaped by `schema`) plus metadata under the key `_meta`."""
    key = hashlib.sha256(json.dumps([MODEL, EFFORT, system, message, schema], sort_keys=True).encode()).hexdigest()
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        cached = json.loads(path.read_text())
        _record(task, cached["_meta"].get("model"), 0, None, cached=True)
        return cached
    if _stopped:
        raise LLMUnavailable(_stopped[0])
    which = backend()
    if which == "none":
        raise LLMUnavailable("No LLM backend: set ANTHROPIC_API_KEY or install and sign in to the claude CLI.")
    with _lock:
        if calls_made() >= MAX_CALLS:
            raise LLMUnavailable(f"LLM call cap of {MAX_CALLS} reached (READOUT_LLM_MAX_CALLS).")
    start = time.monotonic()
    data, model, cost = (_call_api if which == "api" else _call_cli)(system, message, schema)
    seconds = time.monotonic() - start
    data["_meta"] = {"model": model, "backend": which, "effort": EFFORT, "seconds": round(seconds, 1), "task": task}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    _record(task, model, seconds, cost, cached=False)
    return data


def _call_cli(system: str, message: str, schema: dict) -> tuple[dict, str | None, float | None]:
    claude = claude_path()
    if not claude:
        raise LLMUnavailable("claude CLI not found")
    cmd = [claude, "-p", "--model", MODEL, "--effort", EFFORT, "--system-prompt", system,
           "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--disable-slash-commands", "--no-session-persistence", "--setting-sources", "",
           "--output-format", "json", "--json-schema", json.dumps(schema)]
    # The owner's settings force a maximum effort level through the environment, which beats --effort
    # and makes each call think for minutes. These calls always run at EFFORT.
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_EFFORT", "MAX_THINKING_TOKENS")}
    env["CLAUDE_CODE_EFFORT_LEVEL"] = EFFORT
    with tempfile.TemporaryDirectory() as empty:
        try:
            run = subprocess.run(cmd, input=message, capture_output=True, text=True, timeout=TIMEOUT_S,
                                 cwd=empty, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LLMError(f"claude CLI did not answer ({exc.__class__.__name__})") from exc
    try:
        result = json.loads(run.stdout) if run.stdout.strip() else {}
    except ValueError:
        result = {"is_error": True, "result": run.stdout[:300]}
    if not result or result.get("is_error"):
        msg = str(result.get("result") or run.stderr.strip()[:300] or f"exit code {run.returncode}")
        if any(w in msg.lower() for w in ("login", "auth", "oauth")):
            raise LLMUnavailable("claude CLI is not signed in (run `claude auth login`).")
        if any(w in msg.lower() for w in LIMIT_WORDS):
            _stopped.append(f"claude CLI usage limit: {msg[:200]}")
            raise LLMUnavailable(_stopped[0])
        raise LLMError(f"claude CLI error: {msg[:300]}")
    data = result.get("structured_output")
    if data is None:
        found = re.search(r"\{.*\}", str(result.get("result") or ""), flags=re.S)
        if not found:
            raise LLMError("claude CLI answer had no JSON")
        data = json.loads(found.group(0))
    # The CLI can list a small helper model first; the answering model is the one with the most output.
    usage = result.get("modelUsage") or {}
    model = max(usage, key=lambda m: (usage[m] or {}).get("outputTokens") or 0, default=None)
    return data, model, result.get("total_cost_usd")


def _call_api(system: str, message: str, schema: dict) -> tuple[dict, str | None, float | None]:
    try:
        import anthropic
    except ImportError as exc:
        raise LLMUnavailable("pip install anthropic to use ANTHROPIC_API_KEY") from exc
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=system,
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": message}],
    )
    if response.stop_reason == "refusal":
        raise LLMError("the model declined this request")
    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        data = json.loads(text)
    except ValueError:
        found = re.search(r"\{.*\}", text, flags=re.S)
        if not found:
            raise LLMError("API answer had no JSON")
        data = json.loads(found.group(0))
    return data, response.model, None

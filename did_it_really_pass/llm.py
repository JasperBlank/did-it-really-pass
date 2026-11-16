"""Model calls through the Claude Code CLI in print mode, cached on disk.

The CLI is used because it runs on the Claude login already on the machine.
--strict-mcp-config keeps the user's connectors out of the prompt (they added
100,000 tokens per call). The CLI still adds about 650 tokens of its own.
Cost is computed from the reported token counts at API list prices.
"""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from .paths import DATA

CACHE = DATA / "cache"
PRICE = {  # dollars per million tokens (input, output), list prices November 2026
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


# Inside the desktop app this points at its bundled, current CLI; an old CLI on PATH may not know new models.
def _find_cli():
    if os.environ.get("CLAUDE_CODE_EXECPATH"):
        return os.environ["CLAUDE_CODE_EXECPATH"]
    # Outside the desktop app (a hook in a terminal session, say): prefer the newest CLI the app has bundled.
    bundled = sorted(Path.home().glob("AppData/Local/Claude-Data/*/claude-code/*/*/claude.exe"),
                     key=lambda p: [int(x) for x in p.parts[-3].split(".") if x.isdigit()])
    return str(bundled[-1]) if bundled else "claude"


EXE = _find_cli()


def _env():
    # A CLI started from inside a Claude Code session refuses to run unless these are cleared.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "AI_AGENT", "BAGGAGE"))}
    env["DID_IT_REALLY_PASS_CHILD"] = "1"  # so the live hook never runs on the monitor's own model calls
    return env


def ask(model, system, user, salt="", timeout=600):
    """Return {"text", "input_tokens", "output_tokens", "cost"}; identical calls are read from cache."""
    key = hashlib.sha256("\x00".join([model, system, user, salt]).encode()).hexdigest()[:24]
    path = CACHE / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    r = subprocess.run(
        [EXE, "-p", "--model", model, "--system-prompt", system, "--tools", "",
         "--setting-sources", "", "--strict-mcp-config", "--output-format", "json"],
        input=user, capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=_env(),
        cwd=Path(__file__).parent)
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"CLI gave no JSON: {r.stdout[:300]!r} {r.stderr[:300]!r}")
    if d.get("is_error"):
        raise RuntimeError(f"model call failed: {str(d.get('result'))[:300]}")
    u = d.get("usage") or {}
    tin = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
    tout = u.get("output_tokens", 0)
    pin, pout = PRICE[model]
    out = {"text": d.get("result") or "", "input_tokens": tin, "output_tokens": tout,
           "cost": (tin * pin + tout * pout) / 1e6}
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in reply: {text[:200]!r}")
    return json.loads(m.group(0))

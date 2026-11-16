"""Step 1: collect episodes from Claude Code session logs.

One episode runs from a human request to the agent's final message before the
next human request. Output: data/episodes.jsonl (private, never committed).
"""
import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from .paths import DATA

LOGS = Path.home() / ".claude" / "projects"
SHELL = {"Bash", "PowerShell"}
EDIT = {"Edit", "Write", "MultiEdit"}
# Harness bookkeeping calls: not agent work, dropped before counting.
NOISE = re.compile(r"^(mcp__ccd_|TodoWrite|TaskCreate|TaskUpdate|TaskList|ToolSearch|Skill$)")
NOT_HUMAN = ("<command-", "<local-command", "<task-notification", "[Request interrupted",
             "<system-reminder", "Caveat:", "<side-sessions-event", "<ci-monitor-event",
             "This session is being continued")


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    out.append(b.get("text", ""))
                elif b.get("type") == "image":
                    out.append("[image]")
            else:
                out.append(str(b))
        return "\n".join(out)
    return "" if content is None else str(content)


def human_request(d):
    """Return the request text if this log line is a human message, else None."""
    if d.get("type") != "user" or d.get("isSidechain") or d.get("isMeta") or d.get("isCompactSummary"):
        return None
    origin = d.get("origin") or {}
    if origin and origin.get("kind") != "human":
        return None
    content = (d.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    text = text_of(content).strip()
    if not text or text.startswith(NOT_HUMAN):
        return None
    return text


def key_args(tool, inp):
    """The arguments worth showing a monitor, per tool."""
    inp = inp or {}
    if tool in SHELL:
        return {"command": inp.get("command", "")}
    if tool == "Edit":
        return {"file_path": inp.get("file_path", ""), "old_string": inp.get("old_string", ""),
                "new_string": inp.get("new_string", "")}
    if tool == "Write":
        return {"file_path": inp.get("file_path", ""), "content": inp.get("content", "")}
    if tool == "Read":
        return {"file_path": inp.get("file_path", "")}
    if tool in ("Grep", "Glob"):
        return {k: inp[k] for k in ("pattern", "path", "glob") if k in inp}
    if tool in ("Agent", "Task"):
        return {"description": inp.get("description", "")}
    return {"input": json.dumps(inp, ensure_ascii=False)[:300]}


def episodes_in(path):
    cur = None
    by_id = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("isSidechain") or d.get("type") not in ("user", "assistant"):
            continue
        req = human_request(d)
        if req is not None:
            if cur:
                yield cur
            cur = {"request": req, "steps": [], "interrupted": False, "time": d.get("timestamp", "")}
            by_id = {}
            continue
        if cur is None:
            continue
        content = (d.get("message") or {}).get("content")
        if d["type"] == "user" and (d.get("origin") or {}).get("kind") == "task-notification":
            # A background agent or command reporting back. The agent's later claims may rest on this.
            raw = text_of(content)
            parts = [m.group(1).strip() for tag in ("summary", "result")
                     for m in [re.search(rf"<{tag}>(.*?)(</{tag}>|$)", raw, re.S)] if m]
            cur["steps"].append({"kind": "note", "text": "\n".join(parts) or raw})
            continue
        if d["type"] == "user" and isinstance(content, str) and content.startswith("[Request interrupted"):
            cur["interrupted"] = True
        if not isinstance(content, list):
            continue
        if d["type"] == "assistant" and (d.get("message") or {}).get("model"):
            cur["model"] = d["message"]["model"]
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if d["type"] == "assistant" and t == "text" and b.get("text", "").strip():
                cur["steps"].append({"kind": "say", "text": b["text"].strip()})
            elif d["type"] == "assistant" and t == "tool_use":
                if NOISE.search(b.get("name", "")):
                    continue
                step = {"kind": "tool", "tool": b["name"], "args": key_args(b["name"], b.get("input")),
                        "result": None, "is_error": False}
                by_id[b["id"]] = step
                cur["steps"].append(step)
            elif d["type"] == "user" and t == "tool_result" and b.get("tool_use_id") in by_id:
                step = by_id[b["tool_use_id"]]
                step["result"] = text_of(b.get("content"))
                step["is_error"] = bool(b.get("is_error"))
    if cur:
        yield cur


def usable(ep):
    steps = ep["steps"]
    tools = [s for s in steps if s["kind"] == "tool"]
    if ep["interrupted"] or not steps or steps[-1]["kind"] != "say":
        return False
    if not 3 <= len(tools) <= 30 or any(s["result"] is None for s in tools):
        return False
    if not any(s["tool"] in SHELL for s in tools) or not any(s["tool"] in EDIT for s in tools):
        return False
    return 15 <= len(ep["request"]) <= 3000 and len(steps[-1]["text"]) >= 40


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80)
    ap.add_argument("--per-session", type=int, default=2)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    rng = random.Random(a.seed)

    pool = defaultdict(list)  # project -> episodes
    for proj in sorted(LOGS.iterdir()):
        for f in sorted(proj.glob("*.jsonl")):  # top level only: subagent logs live in subfolders
            eps = [e for e in episodes_in(f) if usable(e)]
            rng.shuffle(eps)
            for e in eps[: a.per_session]:
                e["project"], e["session"] = proj.name, f.stem
                pool[proj.name].append(e)
    print("usable per project:", {k[-30:]: len(v) for k, v in pool.items()})

    # Round-robin over projects so one busy project does not swamp the set.
    for v in pool.values():
        rng.shuffle(v)
    picked = []
    while len(picked) < a.n and any(pool.values()):
        for k in sorted(pool):
            if pool[k] and len(picked) < a.n:
                picked.append(pool[k].pop())
    rng.shuffle(picked)
    for i, e in enumerate(picked):
        e["id"] = f"ep{i:03d}"
        e["final"] = e["steps"].pop()["text"]
        del e["interrupted"]
    out = DATA / "episodes.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for e in picked:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    n_tools = [sum(s["kind"] == "tool" for s in e["steps"]) for e in picked]
    print(f"wrote {len(picked)} episodes; tool calls min/median/max = "
          f"{min(n_tools)}/{sorted(n_tools)[len(n_tools) // 2]}/{max(n_tools)}")
    print("by project:", dict(Counter(e["project"][-30:] for e in picked)))


if __name__ == "__main__":
    main()

"""Live monitor: a Claude Code Stop hook.

Runs each time the agent finishes a turn. Haiku screens the episode that just
ended; if it flags a failure, Opus confirms. On a confirmed flag the agent is
stopped from finishing and shown the flag, so it must fix the problem or
correct what it told the user.

Install as a Stop hook in ~/.claude/settings.json:
  {"type": "command", "command": "python \"C:/path/to/agent-monitor/live.py\"", "timeout": 180}

Every verdict is appended to data/live.jsonl. Any error lets the turn end normally.
Set AGENT_MONITOR_OFF=1 to switch it off without removing the hook.
"""
import json
import os
import sys
import time
from pathlib import Path

from .paths import DATA

LOG = DATA / "live.jsonl"
MIN_TOOLS, MAX_TOOLS = 3, 80
EARLIER = 3  # earlier requests in the session shown to the monitor as context


def log(entry):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(entry, at=time.strftime("%Y-%m-%dT%H:%M:%S")), ensure_ascii=False) + "\n")


def last_episode(transcript, final_message):
    """The episode that just ended, plus a short summary of the ones before it."""
    from .collect import episodes_in
    from .render import clip
    eps = list(episodes_in(transcript))
    if not eps:
        return None, ""
    ep = eps[-1]
    # The transcript can lag behind the conversation: take the final message from the hook input.
    if ep["steps"] and ep["steps"][-1]["kind"] == "say" and (
            not final_message or ep["steps"][-1]["text"].strip() == final_message.strip()):
        final_message = ep["steps"].pop()["text"]
    ep["final"] = final_message or ""
    for s in ep["steps"]:
        if s["kind"] == "tool" and s["result"] is None:
            s["result"] = "(result not in the transcript yet)"
    before = []
    for e in eps[-1 - EARLIER:-1]:
        says = [s["text"] for s in e["steps"] if s["kind"] in ("say", "note")]
        before.append(f"REQUEST: {clip(e['request'], 400)}\nAGENT'S LAST MESSAGE: {clip(says[-1], 1000) if says else '(none)'}")
    return ep, "\n\n".join(before)


def judge(model, ep, before):
    from .llm import ask, parse_json
    from .monitor import MODELS, PROMPT, TYPES
    from .render import render
    text = render(ep, final_chars=6000)
    if before:
        text = ("EARLIER IN THIS SESSION (context only: a check reported here counts as run, and behaviour the "
                "user asked for here counts as asked for):\n\n" + before + "\n\nTHE EPISODE TO JUDGE:\n\n" + text)
    r = ask(MODELS[model], PROMPT, text)
    d = parse_json(r["text"])
    if d.get("failure") not in TYPES:
        raise ValueError(f"unknown failure type {d.get('failure')!r}")
    return {"failure": d["failure"], "step": d.get("step"), "quote": str(d.get("quote") or ""), "cost": r["cost"]}


def main():
    if os.environ.get("DID_IT_REALLY_PASS_OFF") or os.environ.get("DID_IT_REALLY_PASS_CHILD"):
        return
    hook = json.loads(sys.stdin.read() or "{}")
    if hook.get("stop_hook_active"):
        return  # the agent is already answering a flag: let it finish, never nag twice
    started = time.time()
    entry = {"session": hook.get("session_id"), "cwd": hook.get("cwd")}
    try:
        ep, before = last_episode(hook["transcript_path"], hook.get("last_assistant_message"))
        tools = [s for s in (ep or {}).get("steps", []) if s["kind"] == "tool"]
        if ep is None or not MIN_TOOLS <= len(tools) <= MAX_TOOLS or not ep["final"]:
            return  # nothing worth a model call: a chat turn, or too long to read
        entry.update(request=ep["request"][:200], tools=len(tools))
        screen = judge("haiku", ep, before)
        entry["haiku"] = screen
        final = screen
        if screen["failure"] != "none":
            final = entry["opus"] = judge("opus", ep, before)
        entry["seconds"] = round(time.time() - started, 1)
        log(entry)
        if final["failure"] == "none":
            return
        reason = (
            f"An automatic monitor flagged this turn as a possible {final['failure'].replace('_', ' ')} "
            f"(step {final['step']}). The line it quoted: \"{final['quote'][:400]}\".\n"
            "About half of these flags are wrong, so check it against what you actually ran. If it is right, fix "
            "the problem or correct what you told the user, plainly. If it is wrong, say in one or two sentences "
            "which check or request backs the quoted line, then stop. Either way, tell the user the monitor "
            "raised this.")
        print(json.dumps({"decision": "block", "reason": reason}))
    except Exception as x:  # fail open: a broken monitor must never block the user's work
        log(dict(entry, error=f"{type(x).__name__}: {x}"[:300], seconds=round(time.time() - started, 1)))


if __name__ == "__main__":
    main()

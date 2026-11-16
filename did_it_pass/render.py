"""Step 2: render an episode as compact numbered text for a monitor to read."""
import json
import re
import sys
from .paths import DATA

ERR = re.compile(r"error|fail|traceback|exception|exit code|not found|denied|cannot|assert|fatal|refused|timed? ?out",
                 re.I)


def clip(s, n):
    s = s.replace("\r", "")
    return s if len(s) <= n else s[:n] + f"… (+{len(s) - n} chars)"


def trim_result(text, head=3, tail=3, max_err=4, width=200):
    """Keep the start, the end and any error lines in between."""
    lines = [l for l in (text or "").replace("\r", "").split("\n") if l.strip()]
    if len(lines) <= head + tail + 2:
        return "\n".join(clip(l, width) for l in lines)
    mid = lines[head:-tail]
    errs = [l for l in mid if ERR.search(l)][:max_err]
    out = lines[:head]
    out.append(f"… ({len(mid) - len(errs)} lines trimmed)")
    out += errs + lines[-tail:]
    return "\n".join(clip(l, width) for l in out)


def render_args(step):
    a = step["args"]
    if "command" in a:
        return clip(a["command"], 500)
    if step["tool"] == "Edit":
        return f"{a['file_path']}\n      - {clip(a['old_string'], 300)!r}\n      + {clip(a['new_string'], 300)!r}"
    if step["tool"] == "Write":
        return f"{a['file_path']} ({len(a['content'])} chars)\n      {clip(a['content'], 200)!r}"
    return clip(" ".join(f"{k}={v}" for k, v in a.items()), 300)


def render(ep, final_chars=1500):
    out = [f"REQUEST: {clip(ep['request'], 1500)}", ""]
    for i, s in enumerate(ep["steps"], 1):
        if s["kind"] == "say":
            out.append(f"[{i}] SAY: {clip(s['text'], 400)}")
        elif s["kind"] == "note":
            out.append(f"[{i}] REPORT from a background task: {clip(s['text'], 1500)}")
        else:
            out.append(f"[{i}] TOOL {s['tool']}: {render_args(s)}")
            res = trim_result(s["result"]).replace("\n", "\n      ")
            out.append(f"    RESULT ({'error' if s['is_error'] else 'ok'}): {res or '(empty)'}")
    out += ["", f"[{len(ep['steps']) + 1}] FINAL: {clip(ep['final'], final_chars)}"]
    return "\n".join(out)


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")]


if __name__ == "__main__":
    eps = load(DATA / (sys.argv[1] if len(sys.argv) > 1 else "episodes.jsonl"))
    sizes = sorted(len(render(e)) for e in eps)
    print(f"{len(eps)} episodes; rendered chars min/median/max = {sizes[0]}/{sizes[len(sizes) // 2]}/{sizes[-1]}")
    if len(sys.argv) > 2:
        sys.stdout.reconfigure(encoding="utf-8")
        print(render(next(e for e in eps if e["id"] == sys.argv[2])))

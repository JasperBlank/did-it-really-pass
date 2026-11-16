"""Scan your own Claude Code sessions for the three failures.

  python scan.py                      # last 30 days: Haiku screens, Opus confirms
  python scan.py --days 0             # everything
  python scan.py --confirm none       # Haiku only (cheaper, more false alarms)
  python scan.py --screen none        # rules only, free

Prints the flagged episodes and writes, into data/ (private):
  scan.json         every verdict
  scan_review.md    each flagged episode with the steps around the flag
  scan_labels.csv   one row per flag, with an empty verdict column to fill in
"""
import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import rules
from collect import LOGS, episodes_in
from monitor import MODELS, model_judge
from render import render

HERE = Path(__file__).parent
MAX_TOOLS = 80  # longer episodes are skipped and counted, not cut


def find_episodes(logs, days):
    cutoff = time.time() - days * 86400 if days else 0
    eps, too_long, seen = [], 0, set()  # forked and resumed sessions repeat earlier episodes
    for proj in sorted(p for p in logs.iterdir() if p.is_dir()):
        for f in sorted(proj.glob("*.jsonl")):
            if f.stat().st_mtime < cutoff:
                continue
            for k, e in enumerate(episodes_in(f)):
                tools = [s for s in e["steps"] if s["kind"] == "tool"]
                if (e["interrupted"] or len(tools) < 3 or not e["steps"] or e["steps"][-1]["kind"] != "say"
                        or any(s["result"] is None for s in tools) or e["steps"][-1]["text"].startswith("API Error")):
                    continue
                if (e["time"], e["request"]) in seen:
                    continue
                seen.add((e["time"], e["request"]))
                if len(tools) > MAX_TOOLS:
                    too_long += 1
                    continue
                e.update(id=f"{f.stem[:8]}-{k}", project=proj.name, session=f.stem, final=e["steps"].pop()["text"])
                eps.append(e)
    return eps, too_long


def excerpt(ep, step, around=2):
    """The rendered steps near the flagged one, plus the start of the final message."""
    last = len(ep["steps"]) + 1
    keep = set(range((step or last) - around, (step or last) + around + 1)) | {last}
    out, show = [], False
    for line in render(ep, final_chars=1200).split("\n")[1:]:
        m = re.match(r"\[(\d+)\]", line)
        if m:
            show = int(m.group(1)) in keep
        if show:
            out.append(line)
    return "\n".join(out)


def run(model, eps, workers, label):
    done = []

    def one(e):
        try:
            r = model_judge(MODELS[model], e)
        except Exception as x:  # a failed call must not lose the rest of the scan; rerun picks it up from cache
            r = {"failure": "none", "step": None, "quote": "", "cost": 0.0, "error": str(x)[:200]}
        done.append(1)
        if len(done) % 50 == 0:
            print(f"  {label}: {len(done)}/{len(eps)}", flush=True)
        return r

    with ThreadPoolExecutor(workers) as pool:
        out = dict(zip((e["id"] for e in eps), pool.map(one, eps)))
    errors = sum("error" in p for p in out.values())
    if errors:
        print(f"  {label}: {errors} calls failed or gave no usable reply and count as not flagged")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", type=Path, default=LOGS)
    ap.add_argument("--days", type=int, default=30, help="only sessions changed in the last N days; 0 = all")
    ap.add_argument("--screen", choices=["haiku", "opus", "none"], default="haiku")
    ap.add_argument("--confirm", choices=["opus", "none"], default="opus")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    eps, too_long = find_episodes(a.logs, a.days)
    print(f"{len(eps)} episodes with 3 to {MAX_TOOLS} tool calls"
          + (f" ({too_long} longer ones skipped)" if too_long else ""))
    verdicts = {e["id"]: {"rules": rules.judge(e)} for e in eps}
    flagged = [e for e in eps if verdicts[e["id"]]["rules"]["failure"] != "none"]
    cost = 0.0
    if a.screen != "none":
        screen = run(a.screen, eps, a.workers, a.screen)
        cost += sum(p["cost"] for p in screen.values())
        for i, p in screen.items():
            verdicts[i]["screen"] = p
        flagged = [e for e in eps if screen[e["id"]]["failure"] != "none"]
        print(f"{a.screen} flagged {len(flagged)}")
        if a.confirm != "none" and a.confirm != a.screen and flagged:
            confirm = run(a.confirm, flagged, a.workers, a.confirm)
            cost += sum(p["cost"] for p in confirm.values())
            for i, p in confirm.items():
                verdicts[i]["confirm"] = p
            flagged = [e for e in flagged if confirm[e["id"]]["failure"] != "none"]
            print(f"{a.confirm} confirmed {len(flagged)}")

    out = HERE / "data"
    out.mkdir(exist_ok=True)
    (out / "scan.json").write_text(json.dumps(verdicts, indent=1, ensure_ascii=False), encoding="utf-8")
    labels = out / "scan_labels.csv"  # keep verdicts already given
    old = {r[0]: r[5:7] for r in csv.reader(open(labels, encoding="utf-8")) if len(r) > 5} if labels.exists() else {}
    rows, review = [], []
    for e in sorted(flagged, key=lambda e: e["time"], reverse=True):
        v = verdicts[e["id"]]
        final = v.get("confirm") or v.get("screen") or v["rules"]
        request = " ".join(e["request"].split())[:140]
        rows.append([e["id"], e["time"][:10], e["project"][-40:], final["failure"], final["step"]]
                    + (old.get(e["id"], ["", ""]) + [""])[:2])
        review.append(f"## {e['id']}  {final['failure']} at step {final['step']}\n\n"
                      f"- When: {e['time'][:16]}  Project: {e['project']}\n- Session: {e['session']}\n"
                      f"- Request: {request}\n- Quote: {final['quote'][:300]}\n\n```\n{excerpt(e, final['step'])}\n```\n")
        print(f"{e['time'][:10]}  {final['failure']:<15} step {str(final['step']):<4} {e['id']}  {request[:70]}")
    (out / "scan_review.md").write_text("\n".join(review), encoding="utf-8")
    with open(out / "scan_labels.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([["id", "date", "project", "failure", "step", "verdict (real / not / unsure)", "note"]] + rows)
    by_type = {}
    for r in rows:
        by_type[r[3]] = by_type.get(r[3], 0) + 1
    n_rules = sum(v["rules"]["failure"] != "none" for v in verdicts.values())
    print(f"\n{len(flagged)} of {len(eps)} episodes flagged {by_type}; the rules alone flag {n_rules}; "
          f"cost at list prices ${cost:.2f}")
    print(f"Details: {out / 'scan_review.md'}")


if __name__ == "__main__":
    main()

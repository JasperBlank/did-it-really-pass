"""Show a scanned episode with the conversation before it, for reviewing a flag.

  python context.py <episode id>             # earlier requests and answers in the session, then the episode
  python context.py <episode id> --step 8    # also print step 8's arguments and result untrimmed
  python context.py <episode id> --before 8  # how many earlier episodes to summarise (default 5)
"""
import argparse
import json
import sys

from collect import LOGS, episodes_in
from render import clip, render


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("--step", type=int)
    ap.add_argument("--before", type=int, default=5)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    stem, k = a.id.rsplit("-", 1)
    path = next(f for f in LOGS.glob("*/*.jsonl") if f.stem.startswith(stem))
    eps = list(episodes_in(path))
    ep = eps[int(k)]
    print(f"SESSION {path.stem} in {path.parent.name}; episode {k} of {len(eps)}; started {ep['time']}\n")
    for j in range(max(0, int(k) - a.before), int(k)):
        e = eps[j]
        says = [s["text"] for s in e["steps"] if s["kind"] in ("say", "note")]
        print(f"--- EARLIER episode {j} ---\nREQUEST: {clip(e['request'], 600)}")
        print(f"LAST MESSAGE: {clip(says[-1], 1500) if says else '(none)'}\n")
    last_is_text = ep["steps"] and ep["steps"][-1]["kind"] == "say"  # an interrupted episode has no final message
    ep = dict(ep, final=ep["steps"][-1]["text"] if last_is_text else "(no final message: episode was cut off)",
              steps=ep["steps"][:-1] if last_is_text else ep["steps"])
    print("--- THE FLAGGED EPISODE ---")
    print(render(ep, final_chars=8000))
    if a.step:
        s = ep["steps"][a.step - 1]
        print(f"\n--- STEP {a.step} UNTRIMMED ---")
        print(json.dumps(s.get("args", s.get("text")), indent=1, ensure_ascii=False)[:20000])
        print("RESULT:", (s.get("result") or "")[:20000])


if __name__ == "__main__":
    main()

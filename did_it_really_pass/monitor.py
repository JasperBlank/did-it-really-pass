"""Steps 5 and 6: run the rules and the model monitors on one split.

  python monitor.py --split dev              # rules, Haiku and the bigger model
  python monitor.py --split test --methods rules haiku opus

Writes data/pred_<method>_<split>.json: {episode id: {failure, step, quote, cost}}.
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import rules
from .llm import ask, parse_json
from .paths import DATA
from .render import load, render

HERE = Path(__file__).parent
MODELS = {"haiku": "claude-haiku-5-5", "opus": "claude-opus-5-5"}
TYPES = ("false_success", "stuck_loop", "test_tampering", "none")
PROMPT = (HERE / "prompt.md").read_text(encoding="utf-8")


def model_judge(model, ep):
    # The final message is shown whole: 59 of 80 run past 1,500 characters and the claim can sit at the end.
    text = render(ep, final_chars=6000)
    cost, last = 0.0, None
    for attempt in range(2):
        r = ask(model, PROMPT, text, salt=str(attempt))
        cost += r["cost"]
        try:
            d = parse_json(r["text"])
            if d.get("failure") not in TYPES:
                raise ValueError(f"unknown failure type {d.get('failure')!r}")
            step = d.get("step")
            return {"failure": d["failure"], "step": int(step) if step not in (None, "") else None,
                    "quote": str(d.get("quote") or ""), "cost": cost}
        except (ValueError, TypeError) as x:
            last = x
    return {"failure": "none", "step": None, "quote": "", "cost": cost, "error": str(last)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--methods", nargs="+", default=["rules", "haiku", "opus"])
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    key = json.loads((DATA / "key.json").read_text(encoding="utf-8"))
    eps = [e for e in load(DATA / "planted.jsonl") if key[e["id"]]["split"] == a.split]
    for m in a.methods:
        if m == "rules":
            preds = {e["id"]: dict(rules.judge(e), cost=0.0) for e in eps}
        else:
            with ThreadPoolExecutor(a.workers) as pool:
                preds = dict(zip((e["id"] for e in eps), pool.map(lambda e: model_judge(MODELS[m], e), eps)))
        (DATA / f"pred_{m}_{a.split}.json").write_text(json.dumps(preds, indent=1), encoding="utf-8")
        bad = sum("error" in p for p in preds.values())
        print(f"{m}: {len(preds)} episodes judged" + (f", {bad} unparseable replies" if bad else ""))


if __name__ == "__main__":
    main()

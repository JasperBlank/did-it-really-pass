"""Experiment: does showing the monitor the earlier requests fix its wrong flags?

Takes the flags from the history scan that have a reviewed verdict (data/review_fable_all.json),
judges each again the way the live hook does (with the last three requests and answers as
context), and compares against the review.

  python -m did_it_really_pass.recheck
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from .collect import LOGS, episodes_in
from .live import EARLIER, judge
from .paths import DATA
from .render import clip


def with_context(episode_id):
    stem, k = episode_id.rsplit("-", 1)
    path = next(f for f in LOGS.glob("*/*.jsonl") if f.stem.startswith(stem))
    eps = list(episodes_in(path))
    k = int(k)
    ep = eps[k]
    ep["final"] = ep["steps"].pop()["text"]
    before = []
    for e in eps[max(0, k - EARLIER):k]:
        says = [s["text"] for s in e["steps"] if s["kind"] in ("say", "note")]
        before.append(f"REQUEST: {clip(e['request'], 400)}\nAGENT'S LAST MESSAGE: {clip(says[-1], 1000) if says else '(none)'}")
    return ep, "\n\n".join(before)


def one(row, prompt=None):
    ep, before = with_context(row["id"])
    out = {"id": row["id"], "flag": row["flag"], "truth": row["verdict"], "severity": row["severity"]}
    for model in ("haiku", "opus"):
        try:
            v = judge(model, ep, before, prompt)
            out[model], out[model + "_cost"] = v["failure"], v["cost"]
        except Exception as x:
            out[model] = "error: " + str(x)[:80]
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    reviewed = json.loads((DATA / "review_fable_all.json").read_text(encoding="utf-8"))
    prompt, name = None, "recheck.json"
    with ThreadPoolExecutor(8) as pool:
        rows = list(pool.map(lambda r: one(r, prompt), reviewed))
    (DATA / name).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    flagged = lambda r, m: r[m] not in ("none",) and not r[m].startswith("error")
    for label, keep in (("real", lambda r: r["truth"] == "real"), ("real and serious", lambda r: r["severity"] == "high"),
                        ("not a failure", lambda r: r["truth"] == "not")):
        group = [r for r in rows if keep(r)]
        both = sum(flagged(r, "haiku") and flagged(r, "opus") for r in group)
        print(f"{label}: {len(group)} | Haiku still flags {sum(flagged(r, 'haiku') for r in group)} | "
              f"Opus still flags {sum(flagged(r, 'opus') for r in group)} | both {both}")
    print("errors:", [r["id"] for r in rows if any(str(r[m]).startswith("error") for m in ("haiku", "opus"))])
    for r in rows:
        print(f"  {r['id']:<14} {r['flag']:<15} truth={r['truth']:<5} {r['severity']:<5} haiku={r['haiku']:<15} opus={r['opus']}")


if __name__ == "__main__":
    main()

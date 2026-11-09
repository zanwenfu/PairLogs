"""The work labels of a version, merged: each repository's kind of project and domain; each session's kind of task,
and its kind of project and domain from its repository, or from the session itself where it names none or GitHub no
longer has it. From the batch run's answers (work_labels_batch.py collect), later files first: an answer asked again
through the form replaces one not used. Prints the shares; writes real text (the evidence quotes): keep it where the
data is.

python work_labels_final.py ITEMS.jsonl OUT_DIR RESULT.jsonl ... (later results win where they have an answer)
"""
import collections
import json
import os
import sys
from pathlib import Path


def main() -> None:
    items_path, out_dir, *results = sys.argv[1:]
    answers, cost, calls = {}, 0.0, 0
    for path in results:
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            calls += 1
            cost += r.get("usd") or 0.0
            if r.get("answer") or r["id"] not in answers:
                answers[r["id"]] = r.get("answer")
    repo = {k.split(":", 1)[1].lower(): v for k, v in answers.items() if k.startswith("repo:") and v}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    old = os.umask(0o077)
    shares = {f: collections.Counter() for f in ("repo project", "repo domain", "session task", "session project",
                                                  "session domain", "project from")}
    by_source = collections.defaultdict(collections.Counter)
    n = unlabelled = 0
    with open(out / "sessions.jsonl", "w", encoding="utf-8") as f:
        for line in open(items_path, encoding="utf-8"):
            s = json.loads(line)
            key = s["session_key"]
            task = (answers.get("session:" + key) or {}).get("task")
            r = repo.get((s.get("repo") or "").lower())
            own = answers.get("project:" + key)
            pd, src = (r, "repository") if r else ((own, "the session") if own else (None, "none"))
            row = {"session_key": key, "source": s["source"], "task": task,
                   "project": (pd or {}).get("project"), "domain": (pd or {}).get("domain"), "project_from": src}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
            unlabelled += task is None
            shares["session task"][(task or {}).get("label", "no answer")] += 1
            shares["session project"][(row["project"] or {}).get("label", "no answer")] += 1
            shares["session domain"][(row["domain"] or {}).get("label", "no answer")] += 1
            shares["project from"][src] += 1
            by_source[s["source"]][(task or {}).get("label", "no answer")] += 1
    with open(out / "repos.jsonl", "w", encoding="utf-8") as f:
        for k, v in sorted(answers.items()):
            if k.startswith("repo:"):
                f.write(json.dumps({"repo": k.split(":", 1)[1], **(v or {"project": None, "domain": None})},
                                   ensure_ascii=False) + "\n")
                shares["repo project"][((v or {}).get("project") or {}).get("label", "no answer")] += 1
                shares["repo domain"][((v or {}).get("domain") or {}).get("label", "no answer")] += 1
    os.umask(old)
    print(f"calls {calls}, cost ${cost:.2f} at the batch price; sessions {n}, {unlabelled} with no kind of task")
    for name, c in shares.items():
        tot = sum(c.values())
        print(f"{name} ({tot}): " + ", ".join(f"{k} {v} ({100 * v / tot:.0f}%)" for k, v in c.most_common()))
    for src, c in sorted(by_source.items()):
        tot = sum(c.values())
        print(f"  task, {src} ({tot}): " + ", ".join(f"{k} {100 * v / tot:.0f}%" for k, v in c.most_common(5)))


if __name__ == "__main__":
    main()

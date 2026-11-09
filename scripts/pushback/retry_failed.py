"""Distinct texts the run left without a label, but for an empty text: a request that failed after all its retries, a
refusal that may not repeat (one `cyber_policy` refusal was labelled when asked again), and the content filter's
refusals (the same text is refused every time under the same filter: 30 of 30 asked again, 2026-10-05; they are asked
again for a filter changed since). Up to three rounds, each labelled by label_all.py into labels.retry.jsonl, whose
lines are then added to labels.jsonl (a later line for an id replaces an earlier one). Counts printed; the last line
"retry: done".   retry_failed.py DIR"""
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

D = Path(sys.argv[1])
for round_ in (1, 2, 3):
    last = {}
    for line in open(D / "labels.jsonl", encoding="utf-8"):
        r = json.loads(line)
        last[r["id"]] = r
    todo = {i for i, r in last.items() if r["label"] is None and r["answer"] != "empty"}
    print(f"round {round_}: {len(last):,} distinct texts with a result; without a label: "
          f"{dict(Counter(r['answer'] for r in last.values() if r['label'] is None))}; {len(todo):,} to try again",
          flush=True)
    if not todo:
        break
    with open(D / "texts.retry.jsonl", "w", encoding="utf-8", errors="surrogateescape") as out:
        for line in open(D / "texts.jsonl", encoding="utf-8", errors="surrogateescape"):
            if json.loads(line)["id"] in todo:
                out.write(line)
    (D / "labels.retry.jsonl").unlink(missing_ok=True)
    got = subprocess.run([sys.executable, "-u", str(Path(__file__).with_name("label_all.py")),
                          str(D / "texts.retry.jsonl"), str(D / "labels.retry.jsonl"), "20"])
    if got.returncode:
        sys.exit(f"retry: label_all exit {got.returncode}")
    again = [json.loads(l) for l in open(D / "labels.retry.jsonl", encoding="utf-8")]
    with open(D / "labels.jsonl", "a", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in again))
    print(f"retry: {len(again):,} tried again, {sum(r['label'] is not None for r in again):,} labelled", flush=True)
print("retry: done", flush=True)

"""The labels of distinct texts (labels.jsonl, a later line for an id replacing an earlier one) joined to every typed
row through map.parquet: pushback.parquet (session_key, turn_number, pushback) for the build's
ERRATA_PUSHBACK_LABELS, and meta.json for its changelog (`pushback_labels`): the model, the run's dates, the prompt
and answer format as sent, the texts cut, the counts, the copies whose text differs from the message they copy
(copies.parquet, where present), and the cost. Refuses unless every distinct text has a result, every text without a
label was refused (not failed), and every key is unique.   join_labels.py DIR"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

import label_all
import luna

D = Path(sys.argv[1])
REFUSED = ("content_filter", "cyber_policy", "refused (model)", "empty")   # declined or nothing to read: not a failure
res: dict[str, dict] = {}
for line in open(D / "labels.jsonl", encoding="utf-8"):
    r = json.loads(line)
    res[r["id"]] = r
length = {}
for line in open(D / "texts.jsonl", encoding="utf-8", errors="surrogateescape"):
    r = json.loads(line)
    length[r["id"]] = len(r["text"])
texts = set(length)
missing = texts - set(res)
if missing:
    sys.exit(f"{len(missing):,} distinct texts have no result; nothing written")
failed = Counter(res[i]["answer"] for i in texts if res[i]["label"] is None and res[i]["answer"] not in REFUSED)
if failed:
    sys.exit(f"{sum(failed.values()):,} texts without a label for a reason other than a refusal: {dict(failed)}; "
             "nothing written")
m = pq.read_table(D / "map.parquet").to_pydict()
keys = list(zip(m["session_key"], m["turn_number"]))
if len(set(keys)) != len(keys):
    sys.exit(f"{len(keys) - len(set(keys)):,} repeated (session_key, turn_number) keys; nothing written")
label = [None if res[i]["label"] is None else bool(res[i]["label"]) for i in m["id"]]
pq.write_table(pa.table({"session_key": pa.array(m["session_key"], pa.string()),
                         "turn_number": pa.array(m["turn_number"], pa.int64()),
                         "pushback": pa.array(label, pa.bool_())}), D / "pushback.parquet")
rows = Counter(label)
unread = Counter(res[i]["answer"] for i in m["id"] if res[i]["label"] is None)
distinct = Counter(None if r["label"] is None else bool(r["label"]) for i, r in res.items() if i in texts)
cost = 0.0
for r in res.values():
    if r.get("dep"):
        _, p_in, _, p_out = label_all.DEPLOYMENTS[r["dep"]]
        cost += (r.get("in") or 0) * p_in + (r.get("out") or 0) * p_out
# The content filter in front of the model, where its setting was changed during the run (filter.json, written by
# hand once the deployments were read back with the new policy): the policies and when the change took effect.
content_filter = json.loads((D / "filter.json").read_text()) if (D / "filter.json").exists() else None
copies = None
if (D / "copies.parquet").exists():
    ids = dict(zip(keys, m["id"]))
    c = pq.read_table(D / "copies.parquet").to_pydict()
    copies = Counter()
    for k, n, o in zip(c["session_key"], c["turn_number"], c["copy_of"]):
        copies["rows"] += 1
        mine, orig = ids.get((k, n)), ids.get((k, o))
        if mine is None or orig is None:
            copies["original_or_copy_not_typed"] += 1
        elif mine != orig:
            copies["text_differs"] += 1
            copies["label_differs"] += res[mine]["label"] != res[orig]["label"]
    copies = {x: copies[x] for x in ("rows", "text_differs", "label_differs", "original_or_copy_not_typed")}
cut = [i for i in texts if length[i] > luna.CAP]
cut_rows = sum(1 for i in m["id"] if length[i] > luna.CAP)
day = lambda p: datetime.fromtimestamp(os.stat(p).st_mtime, timezone.utc).strftime("%Y-%m-%d")
start, end = day(D / "texts.jsonl"), day(D / "labels.jsonl")
meta = {"model": "gpt-6-luna", "model_version": "2026-09-22", "service": "Azure OpenAI (Global Standard and US Data "
        "Zone Standard deployments of the same model), default content filter", "date": start if start == end
        else f"{start} to {end}", "reasoning_effort": "none", "instructions": luna.INSTRUCTIONS, "answer_format": luna.FORMAT,
        "input": (f"the message's text alone between <message> tags, no session context; a message over "
                  f"{luna.CAP:,} characters cut to its first {luna.HEAD:,} and last {luna.TAIL:,}"),
        "unit": "one call per distinct text; every typed row with that exact text takes its label",
        "cut": {"over": luna.CAP, "head": luna.HEAD, "tail": luna.TAIL, "distinct": len(cut), "rows": cut_rows},
        **({"copies": copies} if copies is not None else {}),
        **({"content_filter": content_filter} if content_filter is not None else {}),
        "distinct_texts": len(texts), "distinct_pushback": distinct[True], "distinct_not": distinct[False],
        "distinct_unread": distinct[None], "rows": len(keys), "rows_pushback": rows[True], "rows_not": rows[False],
        "rows_unread": dict(unread), "cost_usd_list_price": round(cost, 2)}
(D / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
print(f"{len(keys):,} typed rows: {rows[True]:,} pushback, {rows[False]:,} not, {rows[None]:,} unread {dict(unread)}; "
      f"{len(texts):,} distinct texts ({distinct[True]:,} / {distinct[False]:,} / {distinct[None]:,}); "
      f"{len(cut):,} texts cut ({cut_rows:,} rows); copies {copies}; ${cost:,.2f} at list price; {meta['date']}")

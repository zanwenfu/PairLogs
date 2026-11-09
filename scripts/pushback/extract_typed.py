"""Every developer-typed message of a version's conversations (typed_by_developer true), its exact text labelled once:
TEXTS (id, text, how many rows) and MAP (session_key, turn_number, id). Counts printed.
    extract_typed.py VERSION_DIR TEXTS_JSONL MAP_PARQUET"""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

V, TEXTS, MAP = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
seen, uses, keys = {}, Counter(), {"session_key": [], "turn_number": [], "id": []}
chars = 0
for p in sorted((V / "conversations").rglob("*.parquet")):
    t = pq.read_table(p, columns=["session_key", "turn_number", "content"], filters=[("typed_by_developer", "=", True)])
    for k, n, text in zip(t["session_key"].to_pylist(), t["turn_number"].to_pylist(), t["content"].to_pylist()):
        text = text or ""
        i = hashlib.sha256(text.encode("utf-8", errors="surrogateescape")).hexdigest()[:24]
        if i not in seen:
            seen[i] = text
            chars += min(len(text), 6000)
        uses[i] += 1
        keys["session_key"].append(k)
        keys["turn_number"].append(n)
        keys["id"].append(i)
with open(TEXTS, "w", encoding="utf-8", errors="surrogateescape") as fh:
    for i, text in seen.items():
        fh.write(json.dumps({"id": i, "text": text, "rows": uses[i]}, ensure_ascii=False) + "\n")
pq.write_table(pa.table(keys), MAP)
print(f"{len(keys['id']):,} typed rows, {len(seen):,} distinct texts; average {chars / max(1, len(seen)):,.0f} "
      f"characters sent per text (capped at 6,000); the commonest: "
      + ", ".join(f"{uses[i]:,}x" for i, _ in uses.most_common(5)))

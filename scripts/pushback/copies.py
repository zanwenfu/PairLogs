"""Typed rows that copy an earlier row of their session (`copy_of` set; a queued message and its delivered copy, for
one), for join_labels.py's count of copies whose text, and so perhaps whose label, differs from the message they copy.
    copies.py VERSION_DIR OUT_PARQUET"""
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

V, OUT = Path(sys.argv[1]), Path(sys.argv[2])
keys = {"session_key": [], "turn_number": [], "copy_of": []}
for p in sorted((V / "conversations").rglob("*.parquet")):
    t = pq.read_table(p, columns=["session_key", "turn_number", "copy_of"], filters=[("typed_by_developer", "=", True)])
    for k, n, c in zip(t["session_key"].to_pylist(), t["turn_number"].to_pylist(), t["copy_of"].to_pylist()):
        if c is not None:
            keys["session_key"].append(k)
            keys["turn_number"].append(n)
            keys["copy_of"].append(c)
pq.write_table(pa.table({"session_key": pa.array(keys["session_key"], pa.string()),
                         "turn_number": pa.array(keys["turn_number"], pa.int64()),
                         "copy_of": pa.array(keys["copy_of"], pa.int64())}), OUT)
print(f"{len(keys['session_key']):,} typed rows copy an earlier row of their session")

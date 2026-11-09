"""Whether two folders hold the same files under the paths given (relative): Parquet files by their tables' content
(schema and rows), any other file byte for byte. Prints each path's verdict; exits 1 if any differs or is missing.

python same_files.py ROOT_A ROOT_B PATH [PATH ...]
"""
import sys
from pathlib import Path

import pyarrow.parquet as pq


def same(a: Path, b: Path) -> bool:
    if a.suffix == ".parquet":
        ta, tb = pq.read_table(a), pq.read_table(b)
        return ta.schema.equals(tb.schema) and ta.equals(tb)
    return a.read_bytes() == b.read_bytes()


def main() -> int:
    ra, rb, *paths = sys.argv[1:]
    bad = 0
    for p in paths:
        a, b = Path(ra) / p, Path(rb) / p
        ok = a.is_file() and b.is_file() and same(a, b)
        bad += not ok
        print(f"{'same' if ok else 'DIFFERENT'}: {p}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

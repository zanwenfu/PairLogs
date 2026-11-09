"""Tier B's labels/swe_chat_prompts.parquet made again from the matching's output (collect.tier_labels.swe_chat_prompts),
written beside and moved over, and the tier's changelog note (labels.swe_chat_prompts) given its new counts. Prints the
counts before and after.

python put_swe_prompts.py KEEP.parquet TIER_B
"""
import json
import os
import sys
from pathlib import Path

from collect import tier_labels


def main() -> int:
    keep, b = Path(sys.argv[1]), Path(sys.argv[2])
    dst = b / "labels" / "swe_chat_prompts.parquet"
    tmp = dst.with_name(dst.name + ".tmp")
    got = tier_labels.swe_chat_prompts(keep, tmp)
    os.replace(tmp, dst)
    log = b / "changelog.json"
    c = json.loads(log.read_text())
    note = c["labels"]["swe_chat_prompts"]
    print("before:", json.dumps({k: note[k] for k in ("prompts", "written_by")}))
    note.update(got)
    print("after: ", json.dumps(got))
    t = log.with_name(log.name + ".tmp")
    t.write_text(json.dumps(c, indent=1) + "\n")
    os.replace(t, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())

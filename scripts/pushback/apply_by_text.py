"""A version's typed rows take the stored pushback labels by their text's id (collect.pushback.apply): the
store gpt-6-luna wrote (on Azure OpenAI for 2026-10-04 and 2026-10-11, on AWS Bedrock for the texts new in 2026-10-27;
a text is labelled once, in any version; a later line for an id replaces an earlier one). The labelling record of 2026-10-11 goes to the changelog with this version's counts, a dated entry is
added to its corrections, and the card is written again from the changelog (collect.card.write). The changelog and
card are kept in BACKUP first; a conversation file is rewritten only where its column changes.

python apply_by_text.py VERSION_DIR STORE OLD_CHANGELOG BACKUP [--dry] [--no-corrected] [--meta EXTRA.json]
With --no-corrected (a version whose labels are joined after its build as the procedure, not as a fix), no entry is
added to the changelog's corrections. With --meta, EXTRA.json's fields are set in the labelling record last (a store
written by more than one service names each); "{version}" in its strings becomes the version's name."""
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

from collect import card, pushback

V, STORE, OLD, BK = map(Path, sys.argv[1:5])
DRY = "--dry" in sys.argv
CORRECTED = "--no-corrected" not in sys.argv
EXTRA = json.loads(Path(sys.argv[sys.argv.index("--meta") + 1]).read_text()) if "--meta" in sys.argv else {}
labels = pushback.load(STORE)
why = pushback.answers(STORE)
files = sorted((V / "conversations").rglob("*.parquet"))
res = pushback.apply(files, labels, write=not DRY)
c = res["counts"]
refused_rows = Counter()
for i, n in res["refused"].items():
    refused_rows[why.get(i) or "refused"] += n
new_rows = c.get("typed, no result yet", 0)
print(json.dumps({"counts": c, "refused rows by reason": dict(refused_rows), "new texts": len(res["missing"]),
                  "files": len(files)}, indent=1))
if DRY:
    sys.exit(0)

BK.mkdir(parents=True, exist_ok=True)
for f in ("changelog.json", "README.md"):
    if not (BK / f).exists():
        shutil.copy2(V / f, BK / f)
ch = json.loads((V / "changelog.json").read_text())
# The labelling record in the build's own form, as scripts/weekly/label.py writes it (release.public_changelog reads
# rows, rows_pushback, rows_not and rows_unread); a published changelog's counts (typed_rows, ...) are left out.
PUBLIC_COUNTS = ("typed_rows", "pushback", "not_pushback", "no_label", "no_label_why")
meta = {k: v for k, v in json.loads(OLD.read_text())["pushback_labels"].items() if k not in PUBLIC_COUNTS}
typed = c.get("pushback", 0) + c.get("not pushback", 0) + c.get("typed, refused", 0) + new_rows
unread = {**dict(refused_rows), **({"new since the labelling, not yet read": new_rows} if new_rows else {})}
meta.update({
    "rows": typed, "rows_pushback": c.get("pushback", 0), "rows_not": c.get("not pushback", 0), "rows_unread": unread,
    "carried": f"to version {V.name} by each row's text (its SHA-256), from the labels written for 2026-10-04 and "
               "2026-10-11; no text was sent again",
})
meta.update({k: v.replace("{version}", V.name) if isinstance(v, str) else v for k, v in EXTRA.items()})
ch["pushback_labels"] = meta
if CORRECTED:
    ch.setdefault("corrected", []).append({
        "date": "2026-10-27",
        "how": "corrected in place: the conversations' `pushback` column and these counts; no text changed",
        "why": "the build ran without the pushback labels, which are joined to a version after it is built; they were "
               "joined by each typed row's text from the labels already written",
        "rows": {"typed rows given a label": c.get("pushback", 0) + c.get("not pushback", 0),
                 "typed rows refused before, left empty": c.get("typed, refused", 0),
                 "typed rows new since the labelling, left empty": new_rows},
    })
(V / "changelog.json").write_text(json.dumps(ch, indent=1, ensure_ascii=False))   # as build.py writes it
card.write(V, ch, log=print)
print("changelog and card written; built_at kept:", ch["built_at"])

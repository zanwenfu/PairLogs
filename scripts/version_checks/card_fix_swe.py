"""A version's card and changelog corrected in place (originals kept in BACKUP), no published text changed: SWE-chat
v2's secret count. A build that reuses collected data starts SWE-chat v2 from version 2026-10-11's masked copy, so it
counts only the spans it masks itself (70); the 194,755 masked in 2026-10-11 are carried over in that copy. The general
form of card_fix_1027.py's count part (the not-secrets part is not needed once the build reads the 325-line list).

python card_fix_swe.py VERSION_DIR BACKUP [--dry]
"""
import json
import os
import shutil
import sys
from pathlib import Path

B, BK = map(Path, sys.argv[1:3])
DRY = "--dry" in sys.argv
CARRIED = 194755
c = json.loads((B / "changelog.json").read_text())
e = c["copied"]["swe-chat-v2"]
own = sum(e["redactions"]["secrets"].values())
assert own == 70 and "masked in version 2026-10-11, carried over" not in e["redactions"]["secrets"], e["redactions"]
tot_was = sum(sum(x["redactions"]["secrets"].values()) for x in c["copied"].values())
tot_now = tot_was + CARRIED
OLD_NOTE = f"{own} secret spans redacted, nothing else changed."
NEW_NOTE = (f"{own + CARRIED:,} secret spans redacted ({CARRIED:,} of them in version 2026-10-11, whose masked copy "
            f"this build started from, and {own} more), nothing else changed.")
assert e["note"].count(OLD_NOTE) == 1
e["redactions"]["secrets"]["masked in version 2026-10-11, carried over"] = CARRIED
e["note"] = e["note"].replace(OLD_NOTE, NEW_NOTE)
c.setdefault("corrected", []).append({
    "date": "2026-10-28",
    "how": "corrected in place: counts and notes only; no published file's text changed",
    "why": ("this build's copy of SWE-chat v2 started from version 2026-10-11's masked copy, so its count held only "
            f"the {own} spans this build masked"),
    "counts": {"swe-chat-v2 secret spans redacted": {"was": own, "now": own + CARRIED},
               "copied datasets' files, secret spans redacted": {"was": tot_was, "now": tot_now}},
})
r = (B / "README.md").read_text(encoding="utf-8")
subs = [(f"| copied datasets' files (32) | {tot_was:,} | 0 | 0 |", f"| copied datasets' files (32) | {tot_now:,} | 0 | 0 |"),
        (f"| swe-chat-v2 (copied files) | {own} | 0 | 0 |", f"| swe-chat-v2 (copied files) | {own + CARRIED:,} | 0 | 0 |"),
        (OLD_NOTE, NEW_NOTE)]
for a, b in subs:
    assert r.count(a) == 1, (a, r.count(a))
    r = r.replace(a, b)
print(f"swe-chat-v2 {own} -> {own + CARRIED:,}; copied total {tot_was:,} -> {tot_now:,}; {len(subs)} card lines")
if DRY:
    sys.exit(0)
BK.mkdir(parents=True, exist_ok=True)
for f in ("changelog.json", "README.md"):
    if not (BK / f).exists():
        shutil.copy2(B / f, BK / f)
modes = {f: (B / f).stat().st_mode & 0o777 for f in ("changelog.json", "README.md")}
(B / "changelog.json").write_text(json.dumps(c, indent=1, ensure_ascii=False))   # as build.py writes it
(B / "README.md").write_text(r, encoding="utf-8")
for f, m in modes.items():
    os.chmod(B / f, m)
print("changelog and card corrected; built_at kept:", c["built_at"])

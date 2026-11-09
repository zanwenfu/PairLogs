# Checks on a built version

Read-only checks run at Duke on a version after its build (and its pushback labels), beside the version before it.
Each prints counts only, never a message's or a finding's text.

- `audit.py NAME=DIR ...`: the tables agree: duplicates point at kept sessions and hold no rows; a kept session's row
  counts equal its rows in the two conversation tables; no conversation row outside a kept session; our label only
  on typed rows that are not copies; session keys unique.
- `kept_twice.py NAME=DIR ...`: kept sessions sharing an agent and session id, by the sources they span.
- `fill_cmp.py` (env `A`, `B`, `NA`, `NB`, `THRESH`): every column's filled share in two versions, the moves past
  `THRESH`.
- `pb_cover.py NAME=DIR ...`: typed rows with our pushback label and with SWE-chat's.
- `owners_new.py VERSION_DIR LOOKUP.json OUT.txt`: repository owners an owner-type lookup lacks.
- `card_scan.py VERSION_DIR WORKDIR`: the card and changelog, rewritten after the build's check, scanned as the
  build's last round scans them (with a made-up token it must find, so a scan that ran on nothing fails), and searched
  for the check's machine marks.
- `same_conv.py VERSION_DIR`: kept sessions whose whole conversation is the same as another kept session's, by
  sources, size and whether they share a session id. On 2026-10-28: 138 groups, most of 1-3 rows (separate runs of
  one prompt); the DataClaw ones with no repository are the card's "Near-copies stay" (near-copies are matched within
  one repository or one copied export).
- `kdiff.py OLD.json NEW.json`: every number that differs between two results of a paper script, list items matched
  by their text fields.
- `card_fix_swe.py VERSION_DIR BACKUP [--dry]`: a hand correction, not a check: SWE-chat v2's secret count in a
  version built from collected data (the build counts only the spans it masks itself, not the 194,755 already masked
  in 2026-10-11's copy it starts from); originals kept in BACKUP.

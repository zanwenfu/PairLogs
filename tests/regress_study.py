"""Regression of errata-data's Claude Code extractor against the errata-bench study's own rules.

Run where the study (its own repository, pushback-review, since 2026-10-04; errata-bench's `study` branch before)
and SWE-chat v1 (its tables and the study's transcripts) are at hand:

    ERRATA_CORPUS=<SWE-chat v1 corpus> PYTHONPATH=<pushback-review>/src:<errata-data> \
        python tests/regress_study.py <pushback-review>/runs/study-pushback-120/sessions.jsonl

Result on 2026-10-03: the five sets identical in all 112 sessions; 1,629 of 2,386 user_prompt rows, as the study. Not
run again since the differences of 2026-10-12 below.

1. The port: events._typed_sets must equal the study's transcript_prompts, all five sets, per session. Since
   2026-10-12 two differences are by design: a message behind a `<system-reminder>` note is read by what follows it
   (its key leaves the note out, and a developer's message there is typed, not among the others), and an entry Claude
   Code marks as another's or the harness's is among the others (a marked /loop still names its prompt); sessions
   holding either can differ.
2. The count: the study's row_kinds, given the rows errata-data marks as the developer's (in place of the study's
   transcript sets), must keep the same SWE-chat user_prompt rows the study recorded (developer_rows_by_transcript),
   whose sum over the sessions it held a transcript for is the study's 1,629 of 2,386."""
import json
from collections import Counter
from pathlib import Path

try:   # the study's own repository
    from pushback_review.corpus.recover import transcript_path
    from pushback_review import sessions as S
except ImportError:   # errata-bench's `study` branch, where it lived until 2026-10-04
    from errata_bench.corpus.recover import transcript_path
    from errata_bench.study import sessions as S

from collect import events as E

recs = {}
import sys

for line in open(sys.argv[1]):
    try:
        r = json.loads(line)
    except ValueError:
        continue
    if r.get("session_id"):
        recs[r["session_id"]] = r
held = [s for s, r in recs.items() if r.get("transcript")]
print(f"sessions: {len(recs)}, held with a transcript by the study: {len(held)}")
print("the study's recorded sum:", sum(recs[s]["developer_rows_by_transcript"] for s in held))
turns = S.load_turns(held)
set_diff = Counter()
study_total = mine_total = rows_total = 0
diffs = []
for sid in held:
    raw = turns.get(sid) or []
    path = transcript_path(sid)
    study = S.transcript_prompts(sid)
    entries = E._read([("full.jsonl", path)])
    mine = E._typed_sets(entries)
    for name in ("commands", "queued", "other", "automatic"):
        if set(getattr(study, name) or set()) != mine[name]:
            set_diff[name] += 1
    if set(study.typed) != mine["typed"]:
        set_diff["typed"] += 1
    rows, _ = E.claude_code([("full.jsonl", path)], {"session_id": sid, "session_key": sid})
    typed_keys = [E._key(r["content"] or "") for r in rows if r["role"] == "user"
                  and ((r["kind"] == "text" and r["typed_by_developer"]) or r["kind"] == "plan")]
    my = S.Typed(typed=typed_keys,
                 commands={E._key(r["command"]) for r in rows if r["kind"] == "command" and r["typed_by_developer"]},
                 queued={E._key(r["content"]) for r in rows if r["turn_type"] == "queue_operation" and r["typed_by_developer"]},
                 other={E._key(r["content"] or "") for r in rows if r["role"] == "user" and not r["typed_by_developer"]
                        and r["kind"] != "plan"} - set(typed_keys),
                 automatic=mine["automatic"])
    kinds = S.row_kinds(raw, my)
    mine_n = sum(1 for t in raw if t.get("turn_type") == "user_prompt" and t.get("turn_number") in kinds)
    study_n = recs[sid]["developer_rows_by_transcript"]
    rows_total += sum(1 for t in raw if t.get("turn_type") == "user_prompt")
    study_total += study_n
    mine_total += mine_n
    if mine_n != study_n:
        study_kinds = S.row_kinds(raw, study)
        only_study = [t for t in raw if t.get("turn_type") == "user_prompt" and t["turn_number"] in study_kinds
                      and t["turn_number"] not in kinds]
        only_mine = [t for t in raw if t.get("turn_type") == "user_prompt" and t["turn_number"] in kinds
                     and t["turn_number"] not in study_kinds]
        diffs.append((sid, study_n, mine_n, [E._norm(t["content"])[:70] for t in only_study][:3],
                      [E._norm(t["content"])[:70] for t in only_mine][:3]))
print(f"1. port of transcript_prompts: sessions where a set differs: {dict(set_diff) or 'none'}")
print(f"2. user_prompt rows the developer typed: study {study_total} of {rows_total}, errata-data {mine_total}")
print(f"   sessions that differ: {len(diffs)}")
for d in diffs[:12]:
    print("   ", d[0][:8], "study", d[1], "mine", d[2], "| study only:", d[3], "| mine only:", d[4])

#!/bin/sh
# Score the person's three checks against version 2026-11-02's keys, once the pages' "Export marks" files are in
# ~/Downloads (the newest of each name is used), and write each scorer's numbers as JSON beside the paper's other
# results, where paper/numbers.py reads them. Each scorer refuses until every card of its check is marked.
# Usage: scripts/e1_check/score_checks.sh   (from the repository's root)
set -u
K=runs/e1-check
OUT=runs/e1-results/paper-1102
newest() { ls -t "$HOME"/Downloads/$1*.json 2>/dev/null | head -1; }
M1=$(newest e1-check-marks-a22cc1d8928c9fe3)
M3=$(newest e1-check3-marks-54761e8375102fae)
M4=$(newest e1-check4-marks-)
echo "who typed: ${M1:-not exported yet}"
[ -n "$M1" ] && python3 scripts/e1_check/agreement.py $K/e1_check_key_2026-11-02.json "$M1" --json $OUT/check_typed.json
echo "pushback: ${M3:-not exported yet}"
[ -n "$M3" ] && python3 scripts/e1_check/agreement3_ctx.py $K/e1_check3_key_2026-11-02.json "$M3" \
    runs/e1-results/ctx_test_2026-11-03/cards80_out.jsonl --json $OUT/check_pushback.json
echo "work labels: ${M4:-not exported yet}"
[ -n "$M4" ] && python3 scripts/e1_check/agreement4.py "$M4" $K/e1_check4_key_2026-11-02.json \
    $K/work_labels_final_2026-11-02.jsonl --also $K/work_labels_also_2026-11-02.json --json $OUT/check_work_labels.json
# A second person's marks on the same work-label cards: against the model, and against the first person's marks.
# Written where paper/numbers.py now reads.
OUT2=runs/e1-results/paper-1104fix2
M4L=$(newest e1-check4-labmate-marks-)
echo "work labels, the labmate: ${M4L:-not exported yet}"
if [ -n "$M4L" ]; then
    python3 scripts/e1_check/agreement4.py "$M4L" $K/e1_check4_key_2026-11-02.json $K/work_labels_final_2026-11-02.jsonl \
        --also $K/work_labels_also_2026-11-02.json --json $OUT2/check_work_labels_labmate.json
    [ -n "$M4" ] && python3 scripts/e1_check/agreement4_people.py "$M4" "$M4L" --json $OUT2/check_work_labels_people.json
fi
# Checks 5, 6 and 7 (2026-11-06/07: a fresh weighted draw of our entries; SWE-chat's prompts by agent; the names the
# masking leaves). Each page exports e1-check-marks-e1-check<N>.json; the scorers refuse until every card is marked.
# 2026-11-08: the owner said the exports of 11-08 for checks 5, 6 and 7 were made by a model (Gemini Argon), not a
# person: they are scored under that name (check*_gemini.json) and never as a person's reading. A person's reading of
# check 7 comes from the three-choice page (sample e1-check7b) into check7_names.json, the file the paper reads.
G5=$HOME/Downloads/e1-check-marks-e1-check5.json; G6=$HOME/Downloads/e1-check-marks-e1-check6.json
G7=$HOME/Downloads/e1-check-marks-e1-check7.json
[ -f "$G5" ] && python3 scripts/e1_check/agreement5.py $K/e1_check5_key.json $K/draw5.json "$G5" --json $OUT2/check5_gemini.json >/dev/null
[ -f "$G6" ] && python3 scripts/e1_check/agreement6.py $K/e1_check6_key.json $K/draw6.json "$G6" --json $OUT2/check6_gemini.json >/dev/null
[ -f "$G7" ] && python3 scripts/e1_check/agreement7.py $K/e1_check7_key.json $K/names7/draw7.json $K/names7/candidates.jsonl \
    "$G7" --count person --json $OUT2/check7_names_gemini.json >/dev/null
M5=$(newest e1-check-marks-e1-check5b)
echo "check 5, a person's reading: ${M5:-not exported yet}"
# Check 5 is scored on the person's final marks: correct5.py counts commands and short replies typed into the
# conversation as typed, as the guide says; the scorers and the model comparison read its output (numbers.py requires it).
if [ -n "$M5" ]; then
    C5=$K/e1-check-marks-e1-check5b-corrected-2026-11-09.json
    python3 scripts/e1_check/correct5.py "$M5" $C5 >/dev/null \
        && python3 scripts/e1_check/agreement5.py $K/e1_check5_key.json $K/draw5.json $C5 --json $OUT2/check5_person.json >/dev/null \
        && python3 scripts/e1_check/check5_split.py $K/e1_check5_key.json $K/draw5.json $K/cards5.json $C5 $OUT2/check5_split.json >/dev/null \
        && python3 scripts/e1_check/person_vs_model5.py $C5 $K/rater2/check5.filled.json $OUT2/check5_person_vs_model.json >/dev/null \
        || echo "check 5: the correction or a scorer refused (see its message above)"
fi
# A second person's marks on check 1's 184 cards (2026-11-09: the same cards and guide, page sample e1-check1-person2):
# against the mark, and against the first person's marks.
M1P=$(newest e1-check-marks-e1-check1-person2)
echo "who typed, a second person: ${M1P:-not exported yet}"
if [ -n "$M1P" ]; then
    python3 scripts/e1_check/agreement.py $K/e1_check_key_2026-11-02.json "$M1P" --json $OUT2/check_typed_person2.json >/dev/null \
        || echo "check 1, second person: the scorer refused (see its message above)"
    [ -n "$M1" ] && python3 scripts/e1_check/agreement_people1.py "$M1" "$M1P" --json $OUT2/check_typed_people.json >/dev/null \
        || echo "check 1, two people: the scorer refused (see its message above)"
fi
# A second person's marks on check 5's 84 cards (2026-11-09: the same cards and guide, page sample e1-check5-person2):
# weighted against the mark (agreement5.py), and against the first person's final marks (agreement_people5.py).
M5P=$(newest e1-check-marks-e1-check5-person2)
echo "check 5, a second person: ${M5P:-not exported yet}"
if [ -n "$M5P" ]; then
    python3 scripts/e1_check/agreement5.py $K/e1_check5_key.json $K/draw5.json "$M5P" --json $OUT2/check5_person2.json >/dev/null \
        || echo "check 5, second person: the scorer refused (see its message above)"
    python3 scripts/e1_check/agreement_people5.py $K/e1-check-marks-e1-check5b-corrected-2026-11-09.json "$M5P" \
        --json $OUT2/check5_people.json >/dev/null || echo "check 5, two people: the scorer refused (see its message above)"
fi
# A second person's marks on check 3's 80 pushback cards (2026-11-09: same cards and guide, sample e1-check3-person2):
# against both labels (agreement3_ctx.py) and against the first person's marks (agreement_people3.py).
M3P=$(newest e1-check3-marks-e1-check3-person2)
echo "pushback, a second person: ${M3P:-not exported yet}"
if [ -n "$M3P" ]; then
    python3 scripts/e1_check/agreement3_ctx.py $K/e1_check3_key_2026-11-02.json "$M3P" \
        runs/e1-results/ctx_test_2026-11-03/cards80_out.jsonl --json $OUT2/check_pushback_person2.json >/dev/null \
        || echo "pushback, second person: the scorer refused (see its message above)"
    [ -n "$M3" ] && python3 scripts/e1_check/agreement_people3.py "$M3" "$M3P" --json $OUT2/check_pushback_people.json >/dev/null \
        || echo "pushback, two people: the scorer refused (see its message above)"
fi
# Check 8 (2026-11-09): the typed mark per source where logs record no author (SpecStory, Aider, DataClaw), 30 cards
# the mark calls typed per source, two people on their own pages (e1-check8-person1, e1-check8-person2).
# A card left unmarked counts as can't tell (left out); numbers.py reports how many.
M8A=$(newest e1-check-marks-e1-check8-person1); M8B=$(newest e1-check-marks-e1-check8-person2)
echo "check 8, per source: ${M8A:-first person not exported yet} | ${M8B:-second person not exported yet}"
if [ -n "$M8A" ] && [ -n "$M8B" ]; then
    python3 scripts/e1_check/agreement8.py $K/e1_check8_key.json $K/draw8.json "$M8A" "$M8B" --json $OUT2/check8.json --unmarked-as-D >/dev/null \
        || echo "check 8: the scorer refused (see its message above)"
fi
M=$(newest e1-check-marks-e1-check7b)
echo "check 7, a person's reading: ${M:-not exported yet}"
if [ -n "$M" ]; then
    python3 scripts/e1_check/agreement7.py $K/e1_check7_key.json $K/names7/draw7.json $K/names7/candidates.jsonl "$M" \
        --three --json $OUT2/check7_names.json >/dev/null || echo "check 7: the scorer refused (see its message above)"
fi
chmod 600 $OUT/check_*.json $OUT2/check_*.json 2>/dev/null
exit 0

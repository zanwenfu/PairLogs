"""Cards for the name check (check 7): what the released tier A's masking leaves, read by a person.

python make_cards7.py candidates.jsonl draw7.json OUT_DIR [--per-stratum 25] [--seed e1-names7-2026-11-06]

candidates.jsonl and draw7.json come from names_left.py (the masking's own finder run on 3,000 drawn rows per stratum
of who wrote the text). Per stratum, up to --per-stratum candidates (one occurrence: a string in a row) in md5(seed |
row | string) order, all of them where there are fewer. Each card shows the string marked in the text around it
(300 characters each side), who wrote that text and the source; never the stratum's counts or the deny list. Writes
cards7.json (shown), e1_check7_key.json (per card: stratum, source, string, deny list; never shown).
"""
import argparse
import hashlib
import json
import os

ap = argparse.ArgumentParser()
ap.add_argument("candidates")
ap.add_argument("draw")
ap.add_argument("out_dir")
ap.add_argument("--per-stratum", type=int, default=25)
ap.add_argument("--seed", default="e1-names7-2026-11-06")
args = ap.parse_args()

WHO = {"typed": "typed by the developer", "person other": "the developer's other entries (commands, plans, files)",
       "agent": "the agent's reply", "tool": "a tool call or its output", "harness": "the agent program (harness)",
       "peer": "another agent"}
cands = [json.loads(line) for line in open(args.candidates)]
draw = json.load(open(args.draw))
assert set(c["stratum"] for c in cands) <= set(draw["strata"]), "a candidate from an unknown stratum"
cards, key = [], {}
for stratum in draw["strata"]:
    xs = [c for c in cands if c["stratum"] == stratum]
    xs.sort(key=lambda c: hashlib.md5(f"{args.seed}|{c['session_key']}|{c['turn_id']}|{c['at']}|{c['string']}".encode()).hexdigest())
    for c in xs[:args.per_stratum]:
        cid = "p7-" + hashlib.sha256(f"{args.seed}|{c['session_key']}|{c['turn_id']}|{c['at']}|{c['string']}".encode()).hexdigest()[:12]
        assert cid not in key
        cards.append({"id": cid, "part": 7, "who": WHO[stratum], "source": c["source"], "agent": c["agent"],
                      "before": c["before"], "string": c["string"], "after": c["after"]})
        key[cid] = {k: c[k] for k in ("stratum", "source", "agent", "string", "kind", "on_deny_list", "session_key",
                                      "turn_id", "table")}
cards.sort(key=lambda c: hashlib.sha256((args.seed + "|order|" + c["id"]).encode()).hexdigest())
os.makedirs(args.out_dir, exist_ok=True)
json.dump(cards, open(os.path.join(args.out_dir, "cards7.json"), "w"), indent=1, ensure_ascii=False)
json.dump(key, open(os.path.join(args.out_dir, "e1_check7_key.json"), "w"), indent=1, ensure_ascii=False)
print("cards", len(cards), {s: sum(1 for v in key.values() if v["stratum"] == s) for s in draw["strata"]})

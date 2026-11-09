"""Check 5, the person's final marks (2026-11-09): a command or a short reply the person typed into the conversation
with the agent counts as typed (A), as the labelling guide says. This sets to A the 12 such cards marked B, each listed
with the opening of its text so the change is checked, not inferred. Commands typed into OpenHands' terminal (kind
'shell', outside the conversation), interrupt markers, program output and one long prompt stay as marked.

python scripts/e1_check/correct5.py <the export e1-check-marks-e1-check5b.json> OUT.json
The output keeps the sample name (the scorers accept only the person's page) and lists the cards set to A.
"""
import json
import sys

IN, OUT = sys.argv[1:3]
CHANGE = {   # card: the opening of its text, as the person saw it
    "p2-2dacc868073b": "Continue",
    "p2-49626d056e82": "<command-name>/context</command-name>",
    "p2-86ace211ce95": "1",
    "p2-d1a63623f2ae": "/exit",
    "p2-946e4c3f532c": "y",
    "p2-f03f5999c2a8": "git clone https://github.com/namin/black",
    "p2-d374c4ba3540": "<command-name>/model</command-name>",
    "p2-d4ee9fc00624": ".add scripts/flickr_test/*.py",
    "p2-1df144813674": "<command-name>/exit</command-name>",
    "p2-9562c9e7a36e": "/drop index.js",
    "p2-f35560c483fc": "!git status",
    "p2-ef990d93d3b7": "<command-name>/compact</command-name>",
}
got = json.load(open(IN))
assert got.get("sample") == "e1-check5b" and "corrected" not in got, "the person's own export, uncorrected"
cards = {c["id"]: c for c in json.load(open("runs/e1-check/cards5.json"))}
key = json.load(open("runs/e1-check/e1_check5_key.json"))
for k, opening in CHANGE.items():
    assert got["marks"][k]["answer"] == "B", (k, got["marks"][k])
    assert cards[k]["text"].strip().startswith(opening), (k, cards[k]["text"][:60])
    assert key[k]["kind"] != "shell", k
    got["marks"][k]["answer"] = "A"
# every other B card is left: none is a command or short reply typed into the conversation
rest = [k for k, v in got["marks"].items() if v["answer"] == "B"]
assert len(rest) == 11, len(rest)
got["corrected"] = {"when": "2026-11-09", "by": "the person who marked the page",
                    "rule": "a command or short reply the person typed into the conversation counts as typed (A), "
                            "as the labelling guide says",
                    "cards_B_to_A": sorted(CHANGE)}
json.dump(got, open(OUT, "w"), indent=1, ensure_ascii=False)
print("changed", len(CHANGE), "cards from B to A; B left:", len(rest))

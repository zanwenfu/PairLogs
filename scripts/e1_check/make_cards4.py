"""Cards for the human check of the labels about the work: a fixed-seed random draw of labelled repositories and
sessions from the pilot, each card showing exactly the text the labeller read (work_labels.repo_text and
session_text). Writes cards.json (no model answer) and the key (card -> pilot row id), kept apart.

python make_cards4.py ITEMS REPOS PILOT OUT_CARDS OUT_KEY [--repos 50] [--sessions 50] [--seed S]
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "paper_e1"))
from work_labels import repo_text, session_text  # noqa: E402

ap = argparse.ArgumentParser()
for a in ("items", "repos", "pilot", "out_cards", "out_key"):
    ap.add_argument(a)
ap.add_argument("--repos-n", type=int, default=50)
ap.add_argument("--sessions-n", type=int, default=50)
ap.add_argument("--seed", default="e1-check4-2026-10-26")
args = ap.parse_args()
items = {i["session_key"]: i for i in map(json.loads, open(args.items))}
repos = {r["repo"]: r for r in map(json.loads, open(args.repos))}
labelled = [json.loads(l) for l in open(args.pilot)]
ok = [r for r in labelled if r["status"] == 200 and r["answer"]]
order = lambda x: hashlib.sha256((args.seed + x).encode()).hexdigest()
repo_ids = sorted((r["id"] for r in ok if r["id"].startswith("repo:")), key=order)[:args.repos_n]
sess_ids = sorted((r["id"] for r in ok if r["id"].startswith("session:")), key=order)[:args.sessions_n]
cards, key = [], {}
for n, rid in enumerate(sorted(repo_ids + sess_ids, key=order)):
    cid = f"c{n:03d}"
    kind, ref = rid.split(":", 1)
    text = repo_text(repos[ref]) if kind == "repo" else session_text(items[ref])
    cards.append({"id": cid, "kind": kind, "text": text})
    key[cid] = rid
sample = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
json.dump(cards, open(args.out_cards, "w"), ensure_ascii=False)
json.dump({"sample": sample, "seed": args.seed, "key": key}, open(args.out_key, "w"), indent=1)
print(f"{len(cards)} cards ({len(repo_ids)} repositories, {len(sess_ids)} sessions); sample {sample}")

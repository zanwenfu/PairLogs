"""The work labels through Bedrock's batch service (half the on-demand price): the pilot's prompts and texts
(work_labels.py, from the guide), one item per call, answered as JSON text rather than through the pilot's form (the
form cost about 60% more; an answer off the guide's lists is asked again on demand, as the pilot's 12 of 1,113).

  build:   python work_labels_batch.py build GUIDE REPOS.jsonl ITEMS.jsonl OUT_DIR [--only IDS.txt] [--per-file N]
           OUT_DIR/records-NNN.jsonl (Bedrock's batch input: recordId, modelInput), OUT_DIR/ids.tsv (recordId -> the
           pilot's id: repo:<owner/name>, session:<key>, project:<key>), and the input's size and estimated cost.
  collect: python work_labels_batch.py collect OUT_DIR RESULT.jsonl BATCH_OUTPUT.jsonl.out ...
           Bedrock's output records -> rows as the pilot wrote them (id, status, in, out, usd, answer, raw, error).
  reask:   python work_labels_batch.py reask OUT_DIR --results R.jsonl ... --record-dirs DIR ...
           the calls whose answer was not used, as records asking again through the pilot's form.

Writes real text (the sessions' typed messages): keep its files where the data is, and delete the bucket's copies.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import work_labels as wl  # noqa: E402

# USD per million tokens, Claude Haiku 4.5 through Bedrock's batch service: half its on-demand price.
BATCH_IN, BATCH_OUT = wl.PRICE_IN / 2, wl.PRICE_OUT / 2
# The pilot's input tokens, fitted to the characters of the text shown (pilot.jsonl, 1,113 calls): per kind, tokens
# before any text, and per character. Used for the estimate only; the cost is read from each answer's usage.
FIT = {"repo": (1061, 0.307), "session": (832, 0.291), "project": (1042, 0.311)}
OUT_TOKENS = {"repo": 106, "session": 58, "project": 125}
ASKED = {"repo": {"project", "domain"}, "project": {"project", "domain"}, "session": {"task"}}


def jobs(guide: str, repos: list[dict], items: list[dict]) -> list[tuple[str, str, str]]:
    """(id, system, user) for every repository, every session, and every session with no repository to read (none
    named, or one gone from GitHub: its kind of project and domain read from the session itself), as work_labels.main
    makes them."""
    P = wl.prompts(guide)
    live = {r["repo"].lower() for r in repos if not r.get("gone")}
    out = [("repo:" + r["repo"], P["repo"], wl.repo_text(r)) for r in repos if not r.get("gone")]
    out += [("session:" + s["session_key"], P["session"], wl.session_text(s)) for s in items]
    out += [("project:" + s["session_key"], P["repo"], "No repository; the session itself.\n" + wl.session_text(s))
            for s in items if not (s.get("repo") and s["repo"].lower() in live)]
    return out


def model_input(system: str, user: str) -> dict:
    return {"anthropic_version": "bedrock-2023-05-31", "max_tokens": wl.MAX_TOKENS, "temperature": 0,
            "system": system, "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}]}


def build(a) -> int:
    guide = open(a.guide, encoding="utf-8").read()
    missing = [x for x in wl.PROJECT + wl.DOMAIN + wl.TASK
               if x != "can't tell" and f"`{x}`" not in guide and f"| {x} |" not in guide]
    if missing:
        print(f"the guide does not name these labels as written here: {missing}")
        return 2
    repos = [json.loads(l) for l in open(a.repos, encoding="utf-8")]
    items = [json.loads(l) for l in open(a.items, encoding="utf-8")]
    todo = jobs(guide, repos, items)
    if a.only:
        want = {l.strip() for l in open(a.only, encoding="utf-8") if l.strip()}
        todo = [j for j in todo if j[0] in want]
    ids = [j[0] for j in todo]
    if len(set(ids)) != len(ids):
        print("ids repeat")
        return 2
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    old = os.umask(0o077)
    est_in = est_out = 0.0
    with open(out / "ids.tsv", "w", encoding="utf-8") as idf:
        for n in range(0, len(todo), a.per_file):
            with open(out / f"records-{n // a.per_file:03d}.jsonl", "w", encoding="utf-8") as f:
                for k, (jid, system, user) in enumerate(todo[n:n + a.per_file], start=n):
                    rid = f"r{k:06d}"
                    f.write(json.dumps({"recordId": rid, "modelInput": model_input(system, user)}, ensure_ascii=False) + "\n")
                    idf.write(f"{rid}\t{jid}\n")
                    kind = jid.split(":", 1)[0]
                    base, per = FIT[kind]
                    est_in += base + per * len(user)
                    est_out += OUT_TOKENS[kind]
    os.umask(old)
    kinds = {}
    for j in ids:
        kinds[j.split(":", 1)[0]] = kinds.get(j.split(":", 1)[0], 0) + 1
    usd = est_in / 1e6 * BATCH_IN + est_out / 1e6 * BATCH_OUT
    size = sum(p.stat().st_size for p in out.glob("records-*.jsonl"))
    print(json.dumps({"records": len(todo), "by_kind": kinds, "files": len(list(out.glob("records-*.jsonl"))),
                      "bytes": size, "est_input_tokens": round(est_in), "est_output_tokens": round(est_out),
                      "est_usd_batch": round(usd, 2)}, indent=1))
    return 0


def collect(a) -> int:
    rid_to = dict(l.rstrip("\n").split("\t", 1) for l in open(Path(a.out_dir) / "ids.tsv", encoding="utf-8"))
    seen, rows = set(), []
    for path in a.outputs:
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            rid = r.get("recordId")
            jid = rid_to.get(rid)
            if jid is None or jid in seen:
                continue
            seen.add(jid)
            mo = r.get("modelOutput") or {}
            u = mo.get("usage") or {}
            tin, tout = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
            text = "".join(c.get("text", "") for c in mo.get("content") or [] if c.get("type") == "text")
            used = next((c.get("input") for c in mo.get("content") or [] if c.get("type") == "tool_use"), None)
            err = r.get("error")
            answer = used if used is not None and not err else (wl.parse(text) if text and not err else None)
            # Only the fields the call asks for: a session read for its kind of project often adds its kind of task
            # in its own words (5 of 14 in the test of 11-01), which says nothing of the two asked.
            if isinstance(answer, dict):
                want = ASKED[jid.split(":", 1)[0]]
                answer = {f: v for f, v in answer.items() if f in want} if want <= set(answer) else None
            ok = wl.valid(answer)
            rows.append({"id": jid, "status": 200 if mo and not err else 0, "request_id": rid, "in": tin, "out": tout,
                         "usd": round(tin / 1e6 * BATCH_IN + tout / 1e6 * BATCH_OUT, 6),
                         "answer": answer if ok else None,
                         "raw": json.dumps(answer)[:600] if ok or used is not None else text[:600],
                         "error": None if ok else (str(err)[:300] if err else
                                                   ("off the guide's lists" if answer else "unreadable"))})
    old = os.umask(0o077)
    with open(a.result, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.umask(old)
    missing = [j for j in rid_to.values() if j not in seen]
    print(json.dumps({"answered": len(rows), "missing": len(missing), "readable": sum(1 for r in rows if r["answer"]),
                      "off_or_unreadable": sum(1 for r in rows if not r["answer"]),
                      "input_tokens": sum(r["in"] for r in rows), "output_tokens": sum(r["out"] for r in rows),
                      "usd_batch": round(sum(r["usd"] for r in rows), 4)}, indent=1))
    return 0


def reask(a) -> int:
    """The calls whose answer was not used (off the guide's lists, unreadable, cut short), asked again through the
    pilot's form: the same prompt, the answer given as a tool call whose fields accept only the guide's labels."""
    rows = [json.loads(l) for p in a.results for l in open(p, encoding="utf-8")]
    want = {r["id"] for r in rows if not r["answer"]}
    got = {r["id"] for r in rows}
    srcs = [Path(d) for d in a.record_dirs]
    jid_of = {}
    for d in srcs:
        jid_of.update(dict(l.rstrip("\n").split("\t", 1) for l in open(d / "ids.tsv", encoding="utf-8")))
    missing = {j for j in jid_of.values()} - got   # a call with no answer at all is asked again too
    want |= missing
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    old = os.umask(0o077)
    n = 0
    with open(out / "records-000.jsonl", "w", encoding="utf-8") as f, open(out / "ids.tsv", "w", encoding="utf-8") as idf:
        for d in srcs:
            for p in sorted(d.glob("records-*.jsonl")):
                for line in open(p, encoding="utf-8"):
                    r = json.loads(line)
                    jid = jid_of.get(r["recordId"])
                    if jid not in want:
                        continue
                    form = wl.FORMS["session"] if jid.startswith("session:") else wl.FORMS["repo"]
                    mi = dict(r["modelInput"])
                    mi["tools"] = [{"name": "record_labels", "description": "Record the labels.", "input_schema": form}]
                    mi["tool_choice"] = {"type": "tool", "name": "record_labels"}
                    rid = f"q{n:06d}"
                    f.write(json.dumps({"recordId": rid, "modelInput": mi}, ensure_ascii=False) + "\n")
                    idf.write(f"{rid}\t{jid}\n")
                    n += 1
    os.umask(old)
    print(json.dumps({"asked_again": n, "not_used": len(want - missing), "no_answer": len(missing)}, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("guide")
    b.add_argument("repos")
    b.add_argument("items")
    b.add_argument("out_dir")
    b.add_argument("--only")
    b.add_argument("--per-file", type=int, default=50000)
    c = sub.add_parser("collect")
    c.add_argument("out_dir")
    c.add_argument("result")
    c.add_argument("outputs", nargs="+")
    q = sub.add_parser("reask")
    q.add_argument("out_dir")
    q.add_argument("--results", nargs="+", required=True)
    q.add_argument("--record-dirs", nargs="+", required=True)
    a = ap.parse_args()
    return {"build": build, "collect": collect, "reask": reask}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())

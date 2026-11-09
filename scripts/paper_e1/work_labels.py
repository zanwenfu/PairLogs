"""The work-label pilot: Claude Haiku 4.5 on Bedrock (AWS credit, account checked first by the peer's key check)
labels each repository's kind of project and domain, and each session's kind of task, from the guide (one text for
the model and the person who checks it). One call per item, temperature 0; every call's request id, tokens and cost
at list price recorded; stops before a call that could pass --max-usd. Resumes: items already answered are skipped.

  ( set -a; . <errata-bench>/.env; set +a
    python3 -I p3_label.py --guide G --repos R.jsonl --items I.jsonl --out O.jsonl [--limit N] [--workers 4] \
        [--max-usd 5] )
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
PRICE_IN, PRICE_OUT = 1.0, 5.0  # USD per million tokens, Anthropic's list price for Haiku 4.5 (global route)
REGION = "us-east-1"
MAX_TOKENS = 300
# The guide's lists, exactly as written there (checked against its text at start); answers go through a form whose
# fields accept only these (the pilot's free JSON gave "developer tools" and "tool" as project kinds, 2026-10-26).
PROJECT = ["product", "internal system", "library or tool", "agent tooling", "research code", "writing",
           "coursework or learning", "personal", "can't tell"]
DOMAIN = ["web", "mobile", "ML or AI", "data", "infrastructure", "systems", "games or graphics", "science",
          "developer tools", "writing", "other", "can't tell"]
TASK = ["new feature", "bug fix", "refactor", "tests", "setup or deploy", "writing", "experiments or analysis",
        "understanding", "review", "routine", "can't tell"]


def field(labels: list[str]) -> dict:
    return {"type": "object", "required": ["label", "also", "evidence"], "properties": {
        "label": {"type": "string", "enum": labels},
        "also": {"type": "array", "items": {"type": "string", "enum": labels}, "maxItems": 2},
        "evidence": {"type": "string", "description": "a quote of at most 12 words copied from the text shown"}}}


FORMS = {"repo": {"type": "object", "required": ["project", "domain"],
                  "properties": {"project": field(PROJECT), "domain": field(DOMAIN)}},
         "session": {"type": "object", "required": ["task"], "properties": {"task": field(TASK)}}}
LISTS = {"project": PROJECT, "domain": DOMAIN, "task": TASK}


def section(guide: str, start: str, end: str | None) -> str:
    i = guide.index(start)
    j = guide.index(end, i) if end else len(guide)
    return guide[i:j].strip()


def prompts(guide: str) -> dict[str, str]:
    head = guide[:guide.index("## 1.")].strip()
    out = section(guide, "## Output", "## Open")
    return {
        "repo": head + "\n\n" + section(guide, "## 1.", "## 3.") + "\n\n" + out
        + "\n\nAnswer with one JSON object: {\"project\": {\"label\", \"also\", \"evidence\"}, "
          "\"domain\": {\"label\", \"also\", \"evidence\"}}. Labels exactly as written in the guide.",
        "session": head + "\n\n" + section(guide, "## 3.", "## Output") + "\n\n" + out
        + "\n\nAnswer with one JSON object: {\"task\": {\"label\", \"also\", \"evidence\"}}. Labels exactly as written "
          "in the guide.",
    }


def repo_text(r: dict) -> str:
    return (f"Repository: {r['repo']}\nDescription: {r.get('description') or '(none)'}\n"
            f"Topics: {', '.join(r.get('topics') or []) or '(none)'}\n"
            f"Languages: {', '.join(r.get('languages') or []) or '(none)'}\n"
            f"README (first 2,000 characters):\n{(r.get('readme') or '(none)')[:2000]}")


def session_text(s: dict) -> str:
    msgs = "\n".join(f"[{i + 1}] {m}" for i, m in enumerate(s["typed"])) or "(no typed messages)"
    return f"Agent: {s.get('agent') or 'unknown'}\nThe developer's typed messages, in order:\n{msgs}"


def call(key: str, system: str, user: str, form: dict) -> tuple[int, dict, str]:
    url = (f"https://bedrock-runtime.{REGION}.amazonaws.com/model/{urllib.parse.quote(MODEL, safe='')}/converse")
    body = {"system": [{"text": system}], "messages": [{"role": "user", "content": [{"text": user}]}],
            "inferenceConfig": {"maxTokens": MAX_TOKENS, "temperature": 0},
            "toolConfig": {"tools": [{"toolSpec": {"name": "record_labels", "description": "Record the labels.",
                                                   "inputSchema": {"json": form}}}],
                           "toolChoice": {"tool": {"name": "record_labels"}}}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get("x-amzn-RequestId", "")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        if not isinstance(e, urllib.error.HTTPError):   # a network failure: retried as a 503
            return 503, {"message": f"{type(e).__name__}: {e}"}, ""
        raw = e.read()
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {"message": raw[:300].decode(errors="replace")}
        return e.code, payload, e.headers.get("x-amzn-RequestId", "") if e.headers else ""


def valid(answer: dict | None) -> bool:
    """Every field present and every main label one of the guide's; an `also` off the list is dropped and noted
    (the pilot's one such answer put a domain word under the project's `also`)."""
    if not isinstance(answer, dict) or not answer:
        return False
    for f, v in answer.items():
        if f not in LISTS or not isinstance(v, dict) or v.get("label") not in LISTS[f]:
            return False
        off = [a for a in v.get("also") or [] if a not in LISTS[f]]
        if off:
            v["also"] = [a for a in v["also"] if a in LISTS[f]]
            v["also_dropped"] = off
    return True


def parse(text: str) -> dict | None:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.index("{"):] if "{" in t else t
    try:
        return json.loads(t[t.index("{"):t.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--guide", required=True)
    ap.add_argument("--repos", required=True)
    ap.add_argument("--items", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="at most this many repositories and sessions each")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-usd", type=float, default=5.0)
    args = ap.parse_args()
    key = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
    if not key:
        print("no AWS_BEARER_TOKEN_BEDROCK in the environment")
        return 2
    guide = open(args.guide).read()
    missing = [x for x in PROJECT + DOMAIN + TASK if x != "can't tell" and f"`{x}`" not in guide and f"| {x} |" not in guide]
    if missing:
        print(f"the guide does not name these labels as written here: {missing}")
        return 2
    P = prompts(guide)
    repos = [json.loads(line) for line in open(args.repos)]
    items = [json.loads(line) for line in open(args.items)]
    done, spent = set(), 0.0
    if os.path.exists(args.out):
        for line in open(args.out):
            r = json.loads(line)
            done.add(r["id"])
            spent += r.get("usd") or 0.0
    jobs = [("repo:" + r["repo"], P["repo"], repo_text(r)) for r in repos if not r.get("gone")]
    form_of = lambda jid: FORMS["session"] if jid.startswith("session:") else FORMS["repo"]
    jobs += [("session:" + s["session_key"], P["session"], session_text(s)) for s in items]
    # a session with no repository: its kind of project and domain read from the session itself (the guide)
    jobs += [("project:" + s["session_key"], P["repo"], "No repository; the session itself.\n" + session_text(s))
             for s in items if not (s.get("repo") and "/" in s["repo"])]
    if args.limit:
        jobs = [j for kind in ("repo:", "session:", "project:") for j in [x for x in jobs if x[0].startswith(kind)][:args.limit]]
    jobs = [j for j in jobs if j[0] not in done]
    lock = threading.Lock()
    state = {"spent": spent, "calls": 0, "errors": 0, "stopped": False}
    worst = lambda sys_t, user_t: (len(sys_t) + len(user_t)) / 2 / 1e6 * PRICE_IN + MAX_TOKENS / 1e6 * PRICE_OUT
    print(f"{len(jobs)} calls to make; spent so far ${spent:.4f}; cap ${args.max_usd:.2f}", flush=True)
    out = open(args.out, "a")

    def one(job):
        jid, system, user = job
        with lock:
            if state["stopped"] or state["spent"] + worst(system, user) * args.workers > args.max_usd:
                state["stopped"] = True
                return
        for attempt in range(5):
            status, body, rid = call(key, system, user, form_of(jid))
            if status in (429, 500, 503) and attempt < 4:
                time.sleep(2 ** attempt * 3)
                continue
            break
        u = body.get("usage") or {}
        tin, tout = int(u.get("inputTokens") or 0), int(u.get("outputTokens") or 0)
        usd = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
        content = ((body.get("output") or {}).get("message") or {}).get("content") or []
        used = next((c["toolUse"].get("input") for c in content if "toolUse" in c), None)
        text = "".join(c.get("text", "") for c in content)
        answer = used if used is not None else (parse(text) if status == 200 else None)
        row = {"id": jid, "status": status, "request_id": rid, "in": tin, "out": tout, "usd": round(usd, 6),
               "answer": answer if valid(answer) else None, "raw": json.dumps(answer)[:600] if answer else text[:600],
               "error": None if status == 200 and valid(answer) else
                        ("off the guide's lists" if status == 200 else str(body.get("message", body))[:300])}
        with lock:
            state["spent"] += usd
            state["calls"] += 1
            state["errors"] += status != 200 or row["answer"] is None
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            if state["calls"] % 100 == 0:
                print(f"{state['calls']} calls, ${state['spent']:.4f}, {state['errors']} errors or unreadable", flush=True)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, jobs))
    out.close()
    print(f"done: {state['calls']} calls this run, {state['errors']} errors or unreadable, total spent "
          f"${state['spent']:.4f}" + ("; STOPPED at the cap" if state["stopped"] else ""), flush=True)
    return 3 if state["stopped"] else 0


if __name__ == "__main__":
    sys.exit(main())

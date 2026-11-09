"""A model as a second, independent reader of the person's checks (2026-11-06, after a small
test). Same guide and same card text the person saw; never the
pipeline's answer, never the person's.

python second_rater.py CHECK CARDS.json OUT_MARKS.json [--limit N] [--dry-run] [--max-usd 2.0] [--workers 4]
       --expect-account ACCOUNT_ID --keyfile ENV_FILE

CHECK: "who" (who typed it: checks 1, 5, 6) or "pushback" (check 3). The guide is the page's own (build_sheet.py and
build_sheet3.py), as plain text, without the page's note on time and export. The card is shown as the page shows it:
the agent and source line, then the text (the first 1,500 characters and how many more were not shown); for pushback,
the end of the agent's last reply, then the developer's message. The model answers one letter and one short reason.
OUT_MARKS.json has the page's export shape ({"sample", "marks": {id: {"answer", "note"}}}), so each check's own scorer
reads it; OUT_MARKS.calls.jsonl keeps each call's time, request id, tokens and list-price cost (never the key).
Resumes: cards already answered are skipped. Stops before a call that could pass --max-usd (all calls of the run,
this one's worst case included).
Model: Claude Sonnet 5.5 on Bedrock (global profile), paid by the AWS credit; list price $2 / $10 per million input /
output tokens. Its prompt (about 700 tokens of guide) is under the cache minimum, so nothing is cached.
"""
import argparse
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

MODEL = "global.anthropic.claude-sonnet-5-5"
PRICE_IN, PRICE_OUT = 2.00, 10.00          # $ per million tokens, list
ET = ZoneInfo("America/New_York")

GUIDE_WHO = """Each card shows one message that an agent's log files as coming from the user. Decide who actually wrote the text. Judge only from the text; you will not see what the pipeline decided.
A. A person typed it. Words a developer wrote to the agent, short or long, in any language. Text a person pasted on purpose (an error log, a file path, a spec) counts as typed. A command the person typed (/review, /create_plan @file) counts as typed.
B. A person's action, not typed text. The person approved a plan the agent wrote ("Implement the following plan: ..."), stopped the agent (an interrupt marker), or attached an image or a file.
C. A program or another agent wrote it. Tool calls or tool output, environment details, instruction files (AGENTS.md, CLAUDE.md, skills), the text a command or skill expands into, a hook's or scheduler's message, a report or message from another agent, a transcript handed to a reviewer agent.
D. Can't tell. Use it when the text alone does not say."""

GUIDE_PUSHBACK = """Each card shows the end of the agent's last reply, then the developer's next message. Judge the developer's message as a reaction to that reply. You will not see what either model decided.
A. Pushback. The developer corrects a mistake or a misunderstanding, rejects or undoes the agent's work, reports that it does not work, or steers away from what the agent did or proposed. ("I said X, not Y", "that broke the build", "no, keep the old one", "it still shows the error".)
B. New information or a change of plan, with no sign the agent did anything wrong. The developer adds requirements or context, widens or narrows the scope, or switches to something else because they changed their mind. ("also make it dark mode", "here are the docs", "actually let's do the API first".)
C. Neither. A new task, the next step, a question, an answer to the agent's question, approval. ("yes", "commit and push", "how does this work?", "now add tests".)
D. Can't tell."""

ASK = ("You are one of two independent readers in a check of a research dataset. Read the guide, then the card, and "
       "choose one answer. Reply with the letter alone on the first line (A, B, C or D), then one short sentence "
       "(at most 20 words) on why.")


def system_for(check):
    return ASK + "\n\nGuide:\n" + (GUIDE_WHO if check == "who" else GUIDE_PUSHBACK)


def card_text(check, c):
    more = f"\n[... {c['len'] - len(c['text'])} more characters not shown]" if c.get("len", 0) > len(c["text"]) else ""
    if check == "who":
        meta = f"agent: {c.get('agent') or 'not named'}" + (f" · source: {c['source']}" if c.get("source") else "")
        return f"{meta}\n\nThe message:\n<<<\n{c['text']}{more}\n>>>"
    prev = ("..." + c["prev"]) if c.get("prev") else "(no reply before this message)"
    return (f"agent: {c.get('agent') or 'not named'}\n\nThe agent's last reply (its end):\n<<<\n{prev}\n>>>\n\n"
            f"The developer's message:\n<<<\n{c['text']}{more}\n>>>")


def read_key(path):
    for line in Path(path).read_text().splitlines():
        if line.startswith("AWS_BEARER_TOKEN_BEDROCK="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no AWS_BEARER_TOKEN_BEDROCK in the key file")


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method, url, key, body=None, timeout=90.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json",
        **({"Content-Type": "application/json"} if data is not None else {})})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {"message": raw[:300].decode(errors="replace")}
        return e.code, payload, dict(e.headers or {})
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, {"message": str(e)[:300]}, {}


def key_account(key, region):
    """The account in the system inference profiles' ARNs: the key's own (a free read)."""
    accounts, token = set(), None
    while True:
        q = {"type": "SYSTEM_DEFINED", "maxResults": "1000", **({"nextToken": token} if token else {})}
        st, body, _ = http("GET", f"https://bedrock.{region}.amazonaws.com/inference-profiles?{urllib.parse.urlencode(q)}", key)
        if st != 200:
            raise SystemExit(f"inference profiles: HTTP {st} {str(body.get('message', ''))[:200]}")
        for p in body.get("inferenceProfileSummaries") or []:
            m = re.search(r"^arn:aws:bedrock:[^:]+:(\d{12}):", p.get("inferenceProfileArn", ""))
            if m:
                accounts.add(m.group(1))
        token = body.get("nextToken")
        if not token:
            return sorted(accounts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("check", choices=["who", "pushback"])
    ap.add_argument("cards")
    ap.add_argument("out")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--max-tokens", type=int, default=60)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--expect-account", required=True)   # the AWS account the key must belong to
    ap.add_argument("--keyfile", required=True)   # a .env file holding AWS_BEARER_TOKEN_BEDROCK (read, never printed)
    a = ap.parse_args()

    cards = json.load(open(a.cards))
    out = Path(a.out)
    calls_path = out.with_suffix(".calls.jsonl")
    done = json.load(open(out))["marks"] if out.exists() else {}
    spent = 0.0
    if calls_path.exists():
        spent = sum(json.loads(line).get("usd", 0) for line in calls_path.open())
    todo = [c for c in cards if c["id"] not in done]
    if a.limit:
        todo = todo[:a.limit]
    system = system_for(a.check)
    bodies = {c["id"]: {"anthropic_version": "bedrock-2023-05-31", "max_tokens": a.max_tokens,
                        "system": [{"type": "text", "text": system}],
                        "messages": [{"role": "user", "content": [{"type": "text", "text": card_text(a.check, c)}]}]}
              for c in todo}
    # the worst case of the whole run at list price: characters / 3 as tokens (an overestimate for English) plus the cap
    worst = sum((len(system) + len(b["messages"][0]["content"][0]["text"])) / 3 * PRICE_IN / 1e6
                + a.max_tokens * PRICE_OUT / 1e6 for b in bodies.values())
    print(f"{len(cards)} cards, {len(done)} answered before, {len(todo)} to send; spent before ${spent:.4f}; "
          f"this run's worst case ${worst:.4f}; cap ${a.max_usd:.2f}", flush=True)
    for c in todo:   # a card holds only what the page showed: never the pipeline's or the person's answer
        assert set(c) <= {"id", "part", "agent", "source", "text", "len", "prev"}, (c["id"], set(c))
    if a.dry_run:
        first = todo[0] if todo else None
        print("--- system ---\n" + system + "\n--- first card ---\n" + (bodies[first["id"]]["messages"][0]["content"][0]["text"]
                                                                      if first else "(none)"))
        return
    if spent + worst > a.max_usd:
        raise SystemExit(f"NOT SENT: spent ${spent:.4f} + worst case ${worst:.4f} passes the cap ${a.max_usd:.2f}")
    key = read_key(a.keyfile)
    acct = key_account(key, a.region)
    if acct != [a.expect_account]:
        raise SystemExit("the key bills another account (or none seen): nothing sent")
    print("key's account: the expected one", flush=True)
    url = f"https://bedrock-runtime.{a.region}.amazonaws.com/model/{urllib.parse.quote(MODEL, safe='')}/invoke"
    lock = threading.Lock()
    state = {"spent": spent, "stop": None}

    def ask(c, body):
        for attempt in range(6):
            t0 = time.monotonic()
            started = datetime.now(timezone.utc)
            st, ans, hdr = http("POST", url, key, body)
            if st in (429, 500, 502, 503, 504, 0) and attempt < 5:
                time.sleep(2 ** attempt + 1)
                continue
            break
        u = ans.get("usage") or {}
        usd = (u.get("input_tokens", 0) * PRICE_IN + u.get("output_tokens", 0) * PRICE_OUT
               + u.get("cache_read_input_tokens", 0) * PRICE_IN * 0.1
               + u.get("cache_creation_input_tokens", 0) * PRICE_IN * 1.25) / 1e6
        text = "".join(b.get("text", "") for b in ans.get("content") or [] if b.get("type") == "text").strip()
        # one letter, not a hedge between two ("A or C? ...": the review of 11-07 found two stored as the first letter)
        m = re.match(r"^\s*\**\s*([ABCD])\b(?!\s*\**\s*(?:or|/|,)\s*\**\s*[ABCD]\b)", text)
        rec = {"id": c["id"], "at_et": started.astimezone(ET).strftime("%Y-%m-%d %-I:%M:%S %p"), "http": st,
               "request_id": hdr.get("x-amzn-RequestId") or hdr.get("X-Amzn-Requestid") or "",
               "seconds": round(time.monotonic() - t0, 2), "input_tokens": u.get("input_tokens", 0),
               "output_tokens": u.get("output_tokens", 0), "cache_read": u.get("cache_read_input_tokens", 0),
               "cache_write": u.get("cache_creation_input_tokens", 0), "usd": round(usd, 6),
               "stop_reason": ans.get("stop_reason"), "answer": m.group(1) if m else None,
               "reply": text[:400] if st == 200 else str(ans.get("message") or ans)[:300]}
        return st, m, text, rec

    def one(c):
        # the same prompt asked again (up to twice more) when the reply does not open with its letter; every call is
        # recorded and counted
        for tries in range(3):
            with lock:
                if state["stop"]:
                    return
            st, m, text, rec = ask(c, bodies[c["id"]])
            rec["try"] = tries + 1
            with lock:
                state["spent"] += rec["usd"]
                with calls_path.open("a") as f:
                    f.write(json.dumps(rec) + "\n")
                if st == 200 and m:
                    done[c["id"]] = {"answer": m.group(1), "note": text[m.end():].strip()[:300]}
                    json.dump({"sample": f"{Path(a.cards).stem}-{MODEL}", "model": MODEL, "marks": done},
                              open(out, "w"), indent=1, ensure_ascii=False)
                elif st != 200:
                    state["stop"] = f"HTTP {st}: {rec['reply'][:200]}"
                if state["spent"] > a.max_usd:
                    state["stop"] = f"spent ${state['spent']:.4f} passed the cap"
            if st != 200 or m:
                return

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(one, todo))
    n_ok = sum(1 for c in cards if c["id"] in done)
    print(f"answered {n_ok} of {len(cards)}; spent ${state['spent']:.4f} in all"
          + (f"; STOPPED: {state['stop']}" if state["stop"] else ""), flush=True)


if __name__ == "__main__":
    main()

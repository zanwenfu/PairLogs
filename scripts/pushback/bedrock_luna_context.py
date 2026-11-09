"""The pushback context test (2026-11-03): does our label change when the labeller also sees the agent's last reply?
gpt-6-luna on AWS Bedrock, as bedrock_luna.py calls it (the same instructions, answer schema, no reasoning, 32 output
tokens, read from luna.py's source without running it), in two modes on the same messages, so the only difference is
the context:
- alone: the request the released labels were made with, unchanged;
- context: the same instructions plus one paragraph, and the end of the agent's last reply before the message in
  <agent_reply> tags ahead of the <message>.
Input lines {id, text, prev, ...}; each result appended to OUT as {id, mode, label, answer, in, out, cached, usd};
(id, mode) pairs already in OUT are skipped; no call starts once the spend recorded could pass --max-usd. The bearer
key AWS_BEARER_TOKEN_BEDROCK comes from the environment and is never printed.

python3 -I bedrock_luna_context.py LUNA_PY IN.jsonl OUT.jsonl [--limit N] [--workers 8] [--max-usd 2]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

URL = "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1/responses"
PRICE = (0.10, 0.01, 0.50)   # USD a million tokens, assumed as in bedrock_luna.py (AWS lists no Luna price yet)
CONTEXT = ("\n\nFor context, the end of the agent's last reply before the message is given between <agent_reply> "
           "tags (empty if there was none). Use it only to understand what the message responds to; classify the "
           "message itself.")


def constants(luna_py: Path) -> tuple[str, dict, int, int, int]:
    want, got = {"INSTRUCTIONS", "FORMAT"}, {}
    for node in ast.parse(luna_py.read_text()).body:
        if isinstance(node, ast.Assign):
            if isinstance(node.targets[0], ast.Name) and node.targets[0].id in want:
                got[node.targets[0].id] = ast.literal_eval(node.value)
            elif isinstance(node.targets[0], ast.Tuple) and [t.id for t in node.targets[0].elts] == ["CAP", "HEAD", "TAIL"]:
                got["CAP"], got["HEAD"], got["TAIL"] = ast.literal_eval(node.value)
    return got["INSTRUCTIONS"], got["FORMAT"], got["CAP"], got["HEAD"], got["TAIL"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("luna_py", type=Path)
    ap.add_argument("inp", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--model", default="global.openai.gpt-6-luna")
    a = ap.parse_args()
    key = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
    if not key:
        print("no AWS_BEARER_TOKEN_BEDROCK in the environment")
        return 2
    instructions, fmt, cap, head, tail = constants(a.luna_py)

    def clip(text: str) -> str:
        return text if len(text) <= cap else text[:head] + "\n[...]\n" + text[-tail:]

    done, spent = set(), 0.0
    if a.out.exists():
        for line in open(a.out, encoding="utf-8"):
            r = json.loads(line)
            spent += r.get("usd") or 0.0
            if r.get("label") is not None or r.get("answer") == "refused (model)":
                done.add((r["id"], r["mode"]))
    rows = [json.loads(line) for line in open(a.inp, encoding="utf-8", errors="surrogateescape")]
    if a.limit is not None:
        rows = rows[:a.limit]
    todo = [(r, m) for r in rows for m in ("alone", "context") if (r["id"], m) not in done]
    print(f"{len(todo):,} calls to make; {len(done):,} done before; ${spent:.4f} spent before", flush=True)
    lock, stop = threading.Lock(), threading.Event()
    state = {"spent": spent, "n": 0, "fail": 0}
    fh = open(a.out, "a", encoding="utf-8")
    os.chmod(a.out, 0o600)

    def one(job: tuple[dict, str]) -> None:
        r, mode = job
        if stop.is_set():
            return
        with lock:
            if state["spent"] >= a.max_usd - 0.01:
                stop.set()
                return
        if mode == "alone":
            ins, inp = instructions, f"<message>\n{clip(r['text'])}\n</message>"
        else:
            ins = instructions + CONTEXT
            inp = f"<agent_reply>\n{r.get('prev') or ''}\n</agent_reply>\n<message>\n{clip(r['text'])}\n</message>"
        body = json.dumps({"model": a.model, "instructions": ins, "input": inp, "max_output_tokens": 32,
                           "reasoning": {"effort": "none"}, "text": {"format": fmt}}).encode()
        label, answer, usage = None, "", {}
        for attempt in range(6):
            req = urllib.request.Request(URL, data=body, method="POST", headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    out = json.loads(resp.read())
                usage = out.get("usage") or {}
                parts = [c for item in out.get("output") or [] if item.get("type") == "message"
                         for c in item.get("content") or []]
                if any(c.get("type") == "refusal" for c in parts):
                    answer = "refused (model)"
                    break
                words = "".join(c.get("text", "") for c in parts)
                try:
                    w = json.loads(words)["label"]
                except (ValueError, KeyError, TypeError):
                    w = words.strip()
                label = 1 if w == "pushback" else 0 if w == "not_pushback" else None
                answer = (w or "no answer")[:40]
                break
            except urllib.error.HTTPError as e:
                answer = f"http {e.code}: {e.read()[:120].decode(errors='replace')}"
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(min(60, 2 ** attempt + attempt))
                    continue
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                answer = f"network: {type(e).__name__}"
                time.sleep(min(60, 2 ** attempt + attempt))
        cached = (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0
        tin, tout = usage.get("input_tokens") or 0, usage.get("output_tokens") or 0
        usd = ((tin - cached) * PRICE[0] + cached * PRICE[1] + tout * PRICE[2]) / 1e6
        rec = {"id": r["id"], "mode": mode, "label": label, "answer": answer, "in": tin, "out": tout,
               "cached": cached, "usd": round(usd, 8)}
        with lock:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            state["spent"] += usd
            state["n"] += 1
            state["fail"] += label is None and answer != "refused (model)"

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(one, todo))
    fh.close()
    print(f"end: {state['n']:,} calls, {state['fail']:,} without a label, ${state['spent']:.4f} in all"
          + (" (stopped at the cap)" if stop.is_set() else ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

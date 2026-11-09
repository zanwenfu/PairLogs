"""gpt-6-luna on AWS Bedrock (the runtime's OpenAI-compatible Responses surface, global profile, AWS credit; the
bearer key AWS_BEARER_TOKEN_BEDROCK from the environment, never printed): the request the Azure run sent for the
pushback labels, unchanged (errata-data scripts/pushback/luna.py: its instructions, input, answer schema, no reasoning,
32 output tokens), read from luna.py's source without running it. One call per distinct text; each result appended
to OUT as {id, label, answer, in, out, cached, dep, served, rid, usd}; ids already in OUT are skipped; no call starts once
the spend recorded could pass --max-usd. A failure after the retries is recorded with no label, to ask again.

python3 -I bedrock_luna.py LUNA_PY IN.jsonl OUT.jsonl [--limit N] [--workers 16] [--max-usd 3] [--model M]
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
# USD a million tokens: input, cached input, output. AWS's price file (2026-10-12) does not list GPT-6 Luna yet; its
# GPT-6 Astra and the relay's GPT-6 Sol are Azure's list prices, so Azure's Luna price ($0.10 / $0.01 / $0.50) is
# assumed here and checked against AWS's cost records after the run.
PRICE = (0.10, 0.01, 0.50)


def constants(luna_py: Path) -> tuple[str, dict, int, int, int]:
    """INSTRUCTIONS, FORMAT, CAP, HEAD, TAIL as luna.py defines them (literal expressions only, nothing run)."""
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
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--max-usd", type=float, default=3.0)
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
            if r.get("label") is not None or r.get("answer") in ("refused (model)",):
                done.add(r["id"])
    todo = []
    for line in open(a.inp, encoding="utf-8", errors="surrogateescape"):
        r = json.loads(line)
        if r["id"] not in done:
            todo.append(r)
    if a.limit is not None:
        todo = todo[:a.limit]
    print(f"{len(todo):,} to label; {len(done):,} done before; ${spent:.4f} spent before; model {a.model}", flush=True)
    lock, stop = threading.Lock(), threading.Event()
    state = {"spent": spent, "n": 0, "fail": 0, "t0": time.time()}
    fh = open(a.out, "a", encoding="utf-8")
    os.chmod(a.out, 0o600)

    def one(r: dict) -> None:
        if stop.is_set():
            return
        with lock:
            if state["spent"] >= a.max_usd - 0.01:   # the next call could pass the cap
                stop.set()
                return
        body = json.dumps({"model": a.model, "instructions": instructions,
                           "input": f"<message>\n{clip(r['text'])}\n</message>", "max_output_tokens": 32,
                           "reasoning": {"effort": "none"}, "text": {"format": fmt}}).encode()
        label, answer, usage, rid, served = None, "", {}, "", ""
        for attempt in range(6):
            req = urllib.request.Request(URL, data=body, method="POST", headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    rid = resp.headers.get("x-amzn-RequestId", "")
                    out = json.loads(resp.read())
                usage = out.get("usage") or {}
                served = out.get("model") or ""
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
                raw = e.read()[:300].decode(errors="replace")
                rid = e.headers.get("x-amzn-RequestId", "") if e.headers else ""
                answer = f"http {e.code}: {raw[:120]}"
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(min(60, 2 ** attempt + attempt))
                    continue
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                answer = f"network: {type(e).__name__}"
                time.sleep(min(60, 2 ** attempt + attempt))
        cached = ((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
        tin, tout = usage.get("input_tokens") or 0, usage.get("output_tokens") or 0
        usd = ((tin - cached) * PRICE[0] + cached * PRICE[1] + tout * PRICE[2]) / 1e6
        rec = {"id": r["id"], "label": label, "answer": answer, "in": tin, "out": tout, "cached": cached,
               "dep": f"bedrock:{a.model}", "served": served, "rid": rid, "usd": round(usd, 8)}
        with lock:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            state["spent"] += usd
            state["n"] += 1
            state["fail"] += label is None and answer != "refused (model)"
            if state["n"] % 1000 == 0:
                el = time.time() - state["t0"]
                print(f"{state['n']:,} done, {state['fail']:,} without a label, ${state['spent']:.4f}, "
                      f"{state['n'] / el:.1f}/s", flush=True)

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(one, todo))
    fh.close()
    print(f"end: {state['n']:,} calls, {state['fail']:,} without a label, ${state['spent']:.4f} in all"
          + (" (stopped at the cap)" if stop.is_set() else ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

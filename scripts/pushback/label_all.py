"""Every distinct developer-typed message labelled pushback (1) or not (0) by gpt-6-luna (luna.py), each on its own,
from its text alone. Calls in parallel under the deployment's 2,000 requests a minute; throttling, server errors and
timeouts retried with backoff; each result appended to OUT as it arrives, so a restart skips what is done; a cost cap
stops the run. Two deployments of the same model share the work, each paced under its own limit: the global one
(2,000 a minute) and the US data zone's (667). Progress every 30 seconds.   label_all.py TEXTS_JSONL OUT_JSONL [CAP_USD]"""
import json
import random
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from luna import call

TEXTS = OUT = None   # set from the command line below; an import (join_labels.py) reads only the prices
CAP = 250.0
# deployment: requests a minute (under its limit), and its list price per token: input, cached input, output
# (prices.azure.com, eastus2, 2026-10-05: "6-luna ShortCo ... Std Gl" and "... Std DZ")
DEPLOYMENTS = {"gpt-6-luna": (1900, 0.10e-6, 0.01e-6, 0.50e-6), "gpt-6-luna-dz": (630, 0.11e-6, 0.011e-6, 0.55e-6)}
RPM = sum(d[0] for d in DEPLOYMENTS.values())
lock = threading.Lock()
state = {"done": 0, "labels": {1: 0, 0: 0, None: 0}, "cost": 0.0, "retries": 0, "failed": 0, "stop": False}
next_slot = {name: time.monotonic() for name in DEPLOYMENTS}


def wait_turn() -> str:
    """The deployment with the earliest free slot, that slot taken (one every 60/RPM seconds per deployment), after
    sleeping until it."""
    with lock:
        name = min(next_slot, key=next_slot.get)
        slot = max(next_slot[name], time.monotonic())
        next_slot[name] = slot + 60.0 / DEPLOYMENTS[name][0]
    time.sleep(max(0.0, slot - time.monotonic()))
    return name


def one(item: dict) -> None:
    if state["stop"]:
        return
    if not item["text"].strip():   # nothing to read: no call
        with lock:
            with open(OUT, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"id": item["id"], "label": None, "answer": "empty", "in": 0, "out": 0,
                                     "dep": None}) + "\n")
            state["done"] += 1
            state["labels"][None] += 1
        return
    dep = None
    for attempt in range(8):
        dep = wait_turn()
        try:
            label, word, usage = call(item["text"], model=dep)
            break
        except urllib.error.HTTPError as e:
            retry = e.code in (408, 409, 429, 500, 502, 503, 504)
            after = e.headers.get("retry-after") if e.headers else None
            if not retry:   # Azure's content filter refusing the message: 400, code content_filter
                try:
                    code = (json.loads(e.read() or b"{}").get("error") or {}).get("code")
                except ValueError:
                    code = None
                label, word, usage = None, code or f"http {e.code}", {}
                break
        except Exception:   # timeouts, resets
            after = None
        with lock:
            state["retries"] += 1
        time.sleep(float(after) if after and after.replace(".", "").isdigit() else min(60, 2 ** attempt) + random.random())
    else:
        label, word, usage = None, "gave up", {}
        with lock:
            state["failed"] += 1
    cached = ((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    _, p_in, p_cached, p_out = DEPLOYMENTS[dep]
    cost = (usage.get("input_tokens", 0) - cached) * p_in + cached * p_cached + usage.get("output_tokens", 0) * p_out
    rec = {"id": item["id"], "label": label, "answer": word, "in": usage.get("input_tokens"),
           "out": usage.get("output_tokens"), "dep": dep}
    with lock:
        with open(OUT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        state["done"] += 1
        state["labels"][label] += 1
        state["cost"] += cost
        if state["cost"] > CAP:
            state["stop"] = True


def drop_torn_tail(path: Path) -> int:
    """A run stopped mid-write can leave a last line without its newline; the next run would append onto it. That
    line is cut off (its text is labelled again). The bytes cut."""
    if not path.exists():
        return 0
    data = path.read_bytes()
    if not data or data.endswith(b"\n"):
        return 0
    keep = data.rfind(b"\n") + 1
    with open(path, "r+b") as fh:
        fh.truncate(keep)
    return len(data) - keep


def report(total: int, t0: float) -> None:
    while not state["stop"] and state["done"] < total:
        time.sleep(30)
        with lock:
            d, c = state["done"], state["cost"]
        rate = d / max(1e-9, (time.time() - t0) / 60)
        left = (total - d) / max(rate, 1e-9)
        print(f"{time.strftime('%H:%M:%S')} {d:,}/{total:,} labelled, {rate:,.0f}/min, ${c:,.2f} so far, "
              f"retries {state['retries']:,}, failed {state['failed']:,}, ~{left:,.0f} min left; "
              f"pushback {state['labels'][1]:,}, not {state['labels'][0]:,}, unreadable {state['labels'][None]:,}",
              flush=True)


if __name__ == "__main__":
    TEXTS, OUT = Path(sys.argv[1]), Path(sys.argv[2])
    CAP = float(sys.argv[3]) if len(sys.argv) > 3 else 250.0
    if (torn := drop_torn_tail(OUT)):
        print(f"{torn} bytes of a torn last line cut from {OUT}", flush=True)
    done = set()
    if OUT.exists():
        for line in OUT.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["id"])
            except ValueError:
                pass
    items = [x for x in (json.loads(l) for l in TEXTS.open(encoding="utf-8")) if x["id"] not in done]
    print(f"{time.strftime('%H:%M:%S')} {len(items):,} messages to label ({len(done):,} done before); "
          f"{RPM:,.0f} requests a minute over {len(DEPLOYMENTS)} deployments, cap ${CAP:,.0f}", flush=True)
    t0 = time.time()
    threading.Thread(target=report, args=(len(items), t0), daemon=True).start()
    with ThreadPoolExecutor(max_workers=200) as pool:
        list(pool.map(one, items))
    print(f"{time.strftime('%H:%M:%S')} finished: {state['done']:,} labelled; pushback {state['labels'][1]:,}, not "
          f"{state['labels'][0]:,}, unreadable {state['labels'][None]:,}; failed {state['failed']:,}; "
          f"${state['cost']:,.2f}" + (" (stopped at the cost cap)" if state["stop"] else ""), flush=True)

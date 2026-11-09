"""Conversations rows for every agent format but Claude Code's (collect/events.py), in SWE-chat's shape.

Each format's rows were mapped from its files' own structure, read on the sample (2026-10-03, structure only): one
row per content block the agent sent the model or got back, SWE-chat's roles and turn types, a tool result cut at
100,000 characters. A format's second event stream (Codex's `item_completed`, Copilot's `tool.execution_*`) gives
no rows of its own; it is read only where it tells who typed what.

Turn types: SWE-chat files every user-role message of these agents as `user_prompt` (SWE-chat v1: Codex's 1,263
user rows, environment context and AGENTS.md included; Cursor's, OpenCode's, Gemini CLI's and Copilot CLI's alike),
so these rows do too, and `typed_by_developer`, `actor` and `kind` say who wrote one. Who typed what, per format, by
the agent's own marks (decision 57):

- Codex: the `user_message` events (older versions) or `UserMessage` items (newer); a model-facing user message is
  typed when it carries one's text (34 of 3,545 marks in the sample carry an image, and their text sits inside the
  message), and a mark no model-facing message carries is a typed row of its own (15). No mark in a session (35 of
  444): unknown.
- Gemini CLI: `user` messages; `info`, `error` and `warning` are the CLI's. Its other file shape, `{role, parts}`
  messages (63 of 303 sessions), wraps what the developer typed in `<USER_REQUEST>`.
- Copilot CLI: `user.message` events, less a subagent's (`source` "agent-...", `agentId`, `parentToolCallId`) and
  automation (`source` "autopilot" or "skill-...", `isAutopilotContinuation`, a `<skill-context>` expansion).
  `parentAgentTaskId` is request telemetry, not authorship (agent-note-cli's classifier, whose test fixture is a
  human message carrying one); an unknown `source` is unknown.
- Pi and Oh My Pi: user messages, less a skill's expansion (opens `<skill>`); extensions' messages are their own
  entries. A `!` command the developer ran is a shell row, as Claude Code's.
- OpenCode: user text parts not marked synthetic (OpenCode marks the file contents and tool output it inserts so).
- Cursor: user messages holding `<user_query>`, which Cursor wraps what the developer typed in, less its own text
  in it (facts.cursor_author: its instructions to the agent, an accepted plan, its interface's buttons).
- Devin, Factory Droid: unknown (Devin marks every user line as input, 63 of 63; Factory Droid marks hook output
  and model-only reminders, but not the system notifications it inserts unmarked).
"""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from .events import (RESULT_CUT, SHELL_TOOLS, _json, bash_category, bash_tool, finish, row, tell, tool_fields,
                     user_text_rule)
from .facts import PI_FORK, _iso, _lenient, copilot_author, cursor_author, opencode_doc

MAIN = re.compile(r"(?:^|/)full\.json(?:l(?:\.(\d+))?)?$")
TAG_OPEN = re.compile(r"^\s*<([A-Za-z_][\w-]*)")


def _main(files: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    """The transcript's parts in order: `full.jsonl` (or `full.json`), then `full.jsonl.001`, ... as Entire splits a
    transcript over 50 MB."""
    return sorted((f for f in files if MAIN.search(f[0])), key=lambda f: int(MAIN.search(f[0]).group(1) or 0))


def _lines(files: list[tuple[str, Path]]):
    """(file, line number, object) for every JSON object line of the files, in order."""
    for name, local in files:
        with open(local, "rb") as fh:
            for n, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    value = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    yield name, n, value


def _docs(files: list[tuple[str, Path]]):
    """(file, document) for a transcript stored as one JSON document per part."""
    return [(name, _lenient(local.read_bytes())) for name, local in files]


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def _join(texts: list[str]) -> str:
    """Text blocks joined by newlines, an image's marker appended as SWE-chat appends it ("\\n[Image: image/png]")."""
    return "".join(t if t.startswith("\n[") else ("\n" if i else "") + t for i, t in enumerate(texts)).strip("\n")


def _user(base: dict, text: str, typed: bool | None, actor: str | None, kind: str = "text", **kw) -> dict:
    return row(**base, role="user", turn_type="user_prompt", content=text, kind=kind, actor=actor,
               typed_by_developer=typed, **kw)


def _prompt_row(base: dict, text: str, **kw) -> dict:
    """A user message whose author the format does not record (SpecStory's and Aider's markdown, DataClaw's records,
    Devin's): the developer's, typed, unless its text is a harness's, an IDE's or a tool's (events.user_text_rule),
    its wrapping taken off (events.unwrap). Left unknown before 2026-10-05: 225,000 rows with no author. A message
    with no text at all is the developer's too: SpecStory's export keeps the turn of what they sent but not its content
    (pasted output, an image: the agent's next reply answers it), 1,734 rows of version 2026-10-04 read as the
    harness's until 2026-10-07. It stays typed with no text, and no pushback label (nothing to classify)."""
    if not (text or "").strip():
        return _user(base, text or "", True, "developer", "text", **kw)
    content, typed, actor, kind, command = tell(text)
    return _user(base, content, typed, actor, kind, **({"command": command} if command else {}), **kw)


def _say(base: dict, text, **kw) -> dict:
    return row(**base, role="assistant", turn_type="assistant_response", content=text, kind="text", actor="agent",
               typed_by_developer=False, **kw)


def _think(base: dict, text, **kw) -> dict:
    return row(**base, role="assistant", turn_type="assistant_thinking", content=text or None, kind="thinking",
               actor="agent", typed_by_developer=False, **kw)


def _call(base: dict, name, args, cid, **kw) -> dict:
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except ValueError:
            parsed = args
    else:
        parsed = args
    as_json = _json(parsed if parsed is not None else {})
    return row(**base, role="tool_use", turn_type="tool_use", content=as_json, tool_name=name, tool_call_id=cid,
               tool_input_json=as_json, **tool_fields(name, parsed), kind="tool_call", actor="agent",
               typed_by_developer=False, **kw)


def _out(base: dict, cid, text, *, is_error=None, name=None, **kw) -> dict:
    text = text if isinstance(text, str) else ("" if text is None else _json(text))
    return row(**base, role="tool_result", turn_type="tool_result", tool_call_id=cid, tool_name=name,
               content=text[:RESULT_CUT], content_truncated=len(text) > RESULT_CUT, is_error=is_error,
               kind="tool_result", actor="tool", typed_by_developer=False, **kw)


def _meta(base: dict, turn_type: str, kind: str, content=None, **kw) -> dict:
    return row(**base, role="metadata", turn_type=turn_type, content=content, kind=kind, actor="harness",
               typed_by_developer=False, **kw)


def _ints(**kw) -> dict:
    """Token counts as integers; a count below zero records no count (OpenCode wrote negative output counts in 6
    rows, 2026-10-05)."""
    return {k: int(v) for k, v in kw.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0}


def _place(rows: list[dict], usage: dict, since: int) -> None:
    """One model call's token counts on its first text row, else its first tool call (SWE-chat's placement), among
    the rows from `since` on; none placed when the call left neither."""
    if not usage:
        return
    for turn in ("assistant_response", "tool_use"):
        for r in rows[since:]:
            if r["turn_type"] == turn and r["input_tokens"] is None and r["output_tokens"] is None:
                r.update(usage)
                return


# -- Codex (rollout JSONL) --------------------------------------------------------------------------------------
CODEX_CALLS = {"function_call", "custom_tool_call", "local_shell_call", "web_search_call", "tool_search_call"}
CODEX_OUTPUTS = {"function_call_output", "custom_tool_call_output", "local_shell_call_output", "tool_search_output"}


def _norm(text: str) -> str:
    return " ".join((text or "").split())


# Codex records how a session runs (session_meta's `source`): a subagent's thread or a guardian review (`{"subagent":
# ...}`, 938 of the collection's sessions on 2026-10-04) gets its prompts from a parent agent or the app, never the
# developer; a headless run (`exec`, 624) gives no sign of who wrote them. Its own prompts given as a user's: /review's
# expansion (the developer's command), an orchestrator's. Diff comments and annotations of a response are the
# developer's own, wrapped by the app.
CODEX_REVIEW = "## Code review guidelines:"
CODEX_ANNOTATIONS = ("# Diff comments:", "# Response annotations:")
CODEX_NOT_TYPED = ("You are OMX Explore, a low-cost read-only repository exploration harness",)
# oh-my-codex's team: the leader writes a worker's prompts, given as the user's (54 files of the collection,
# 2026-10-04; a worker runs in `.omx/team/<team>/worktrees/worker-N`), like Claude Code's teammates.
CODEX_TEAM_WORKER = re.compile(r"\s*(?:\d+ new msg\(s\): read|Read) \$OMX_TEAM_STATE_ROOT/team/")


def codex_run(entries) -> str:
    """`subagent`, `headless` or `interactive`, from the session's session_meta."""
    for _, _, e in entries:
        if e.get("type") == "session_meta":
            pl = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            src = pl.get("source")
            if isinstance(src, dict) and "subagent" in src:
                return "subagent"
            if src == "exec" or pl.get("originator") == "codex_exec":
                return "headless"
            return "interactive"
    return "interactive"


# A fork that records no ordinal (`history_mode` legacy, or none: 209 of SWE-chat v2's forked Codex threads,
# 2026-10-07) writes its parent's history in one burst when it is made, each entry stamped within milliseconds of the
# one before; the thread's own entries come after its model's first call, seconds or more later.
CODEX_FORK_BURST = 1.0


def _seconds(value) -> float | None:
    iso = _iso(value)
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else None


def codex_inherited(entries):
    """For a helper thread forked from its parent (session_meta's `forked_from_id`), a test of whether the entry at an
    index is the parent's, carried over: before the ordinal its own history starts at (`subagent_history_start_ordinal`),
    or, in a fork that records none, in the burst its parent's history was written in. Else a test always false."""
    for n, (_, _, e) in enumerate(entries):
        if e.get("type") == "session_meta":
            pl = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            if not pl.get("forked_from_id"):
                break
            start = pl.get("subagent_history_start_ordinal")
            if str(start or "").isdigit():
                return lambda i, x: str(x.get("ordinal") or "").isdigit() and int(x["ordinal"]) < int(start)
            end, before = len(entries), _seconds(e.get("timestamp"))
            for i in range(n + 1, len(entries)):
                t = _seconds(entries[i][2].get("timestamp"))
                if t is None:
                    continue
                if before is not None and t - before > CODEX_FORK_BURST:
                    end = i
                    break
                before = t
            return lambda i, x: i < end
    return lambda i, x: False


def codex(files, session):
    entries = list(_lines(_main(files)))
    run = codex_run(entries)
    inherited = codex_inherited(entries)
    marks: list[tuple] = []   # (entry index, text, normalized text, carries an image, kind of mark)
    for i, (_, _, e) in enumerate(entries):
        pl = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        if e.get("type") != "event_msg":
            continue
        if pl.get("type") == "user_message":
            text = str(pl.get("message") or "")
            marks.append((i, text, _norm(text), bool(pl.get("images") or pl.get("local_images")), "event"))
        elif pl.get("type") == "item_completed" and isinstance(pl.get("item"), dict) and pl["item"].get("type") == "UserMessage":
            content = pl["item"].get("content") or []
            text = _text(content)
            marks.append((i, text, _norm(text), any(isinstance(b, dict) and b.get("type") != "text" for b in content), "item"))
    if len({m[4] for m in marks}) > 1:   # both kinds in one session (none of 444 in the sample): the events only
        marks = [m for m in marks if m[4] == "event"]
    asked = [(i, _norm(_text(e["payload"].get("content")))) for i, (_, _, e) in enumerate(entries)
             if e.get("type") == "response_item" and isinstance(e.get("payload"), dict)
             and e["payload"].get("type") == "message" and e["payload"].get("role") == "user"]
    # A typed mark's model-facing message: the same text; for a mark with an image, the message holding its text.
    by_text: dict[str, deque] = defaultdict(deque)
    for m in marks:
        by_text[m[2]].append(m)
    typed_at, used = set(), set()
    for i, t in asked:
        if by_text.get(t):
            used.add(by_text[t].popleft()[0])
            typed_at.add(i)
    for m in marks:
        if m[0] in used or not m[3] or not m[2]:
            continue
        hit = min((i for i, t in asked if i not in typed_at and m[2] in t), key=lambda i: abs(i - m[0]), default=None)
        if hit is not None:
            used.add(m[0])
            typed_at.add(hit)
    alone = {m[0]: m[1] for m in marks if m[0] not in used}   # typed, but no model-facing message carries it
    # A legacy fork copies its parent's marks with its messages (a fork with an ordinal copies none): there, an
    # inherited message without one is the app's (`<recommended_plugins>`, a context block), as in any marked session.
    marked_inheritance = any(inherited(m[0], entries[m[0]][2]) for m in marks)
    record_kind = "token_usage_record" if any(e.get("type") == "token_usage_record" for _, _, e in entries) else "token_count"
    rows: list[dict] = []
    model, since = None, 0
    for i, (fname, line, e) in enumerate(entries):
        kind = e.get("type")
        pl = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        base = {"raw_file": fname, "raw_line": line, "timestamp": _iso(e.get("timestamp"))}
        if i in alone:
            worker = run == "subagent" or bool(CODEX_TEAM_WORKER.match(alone[i]))
            rows.append(_user(base, alone[i], not worker, "agent" if worker else "developer",
                              "notice" if worker else "text"))
            continue
        if kind == "turn_context":
            model = pl.get("model") or model
            continue
        u = None
        if record_kind == "token_usage_record" and kind == "token_usage_record":
            u = pl.get("usage") if isinstance(pl.get("usage"), dict) else {}
        elif record_kind == "token_count" and kind == "event_msg" and pl.get("type") == "token_count":
            info = pl.get("info") if isinstance(pl.get("info"), dict) else {}
            u = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
        if u is not None:
            usage = _ints(input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"),
                          cache_read_input_tokens=u.get("cached_input_tokens"),
                          cache_creation_input_tokens=u.get("cache_write_input_tokens"))
            if usage:   # a count with nothing in it (rate limits only) belongs to no call
                _place(rows, usage, since)
                since = len(rows)
            continue
        if kind == "session_meta":
            rows.append(_meta(base, "system_event", "notice"))
            continue
        if kind == "compacted":
            rows.append(_meta(base, "summary", "compaction", pl.get("message") if isinstance(pl.get("message"), str) else None))
            continue
        if kind != "response_item":
            continue
        t = pl.get("type")
        base["source_entry_uuid"] = pl.get("id") if isinstance(pl.get("id"), str) else None
        if t == "message":
            content = pl.get("content")
            texts = []
            for b in content if isinstance(content, list) else [{"text": content}]:
                if isinstance(b, dict) and isinstance(b.get("text"), str):
                    texts.append(b["text"])
                elif isinstance(b, dict) and b.get("type") == "input_image":
                    texts.append("\n[Image: unknown]")
            text = _join(texts)
            if not text.strip():
                continue
            if pl.get("role") == "assistant":
                rows.append(_say({**base, "model": model}, text))
            elif run == "subagent" and inherited(i, e):
                # The parent's conversation, carried into a helper thread forked from it (the entries before
                # subagent_history_start_ordinal, or a legacy fork's first burst): the developer's messages there are
                # theirs, copies of the parent thread's (copy_of -1; version 2026-10-04 read them as an agent's: 890 rows
                # in 157 threads with an ordinal, corrected 2026-10-07, and the legacy forks', corrected later that day),
                # the app's by their text or role. A parent agent's first prompt written in the burst (10 of 533 typed
                # ones in SWE-chat v2's legacy forks) reads as a copy.
                if pl.get("role") == "developer":
                    rows.append(_user(base, text, False, "harness", "notice"))
                elif text.lstrip().startswith("<turn_aborted"):
                    rows.append(_user(base, text, False, "developer", "interrupt"))
                elif marked_inheritance and i not in typed_at:
                    rows.append(_user(base, text, False, "harness", "notice"))
                else:
                    rows.append(_user(base, text, True, "developer", copy_of=-1))
            elif run == "subagent":
                rows.append(_user(base, text, False, "agent", "notice"))
            elif i in typed_at and text.lstrip().startswith(CODEX_NOT_TYPED):
                rows.append(_user(base, text, False, "harness", "notice"))
            elif i in typed_at and CODEX_TEAM_WORKER.match(text):
                rows.append(_user(base, text, False, "agent", "notice"))
            elif i in typed_at and text.lstrip().startswith(CODEX_REVIEW):
                rows.append(_user(base, text, True, "developer", "command", command="/review"))
            elif i in typed_at:
                rows.append(_user(base, text, True, "developer",
                                  "annotation" if text.lstrip().startswith(CODEX_ANNOTATIONS) else "text"))
            elif text.lstrip().startswith("<turn_aborted"):
                rows.append(_user(base, text, False, "developer", "interrupt"))   # Codex's notice of the developer's stop
            elif marks or pl.get("role") == "developer":
                rows.append(_user(base, text, False, "harness", "notice"))
            elif user_text_rule(text):
                rule = user_text_rule(text)
                rows.append(_user(base, text, rule["typed"], rule["actor"], rule["kind"],
                                  **({"command": rule["command"]} if rule.get("command") else {})))
            else:   # a session with no typed-message mark: a message no rule names is the developer's (2026-10-07)
                rows.append(_user(base, text, True, "developer"))
        elif t == "reasoning":
            summary = "\n".join(str(s.get("text") or "") for s in pl.get("summary") or [] if isinstance(s, dict))
            rows.append(_think({**base, "model": model}, summary))
        elif t in CODEX_CALLS:
            args = {"function_call": pl.get("arguments"), "custom_tool_call": pl.get("input")}.get(t, pl.get("action"))
            rows.append(_call({**base, "model": model}, pl.get("name") or t.removesuffix("_call"), args, pl.get("call_id")))
        elif t in CODEX_OUTPUTS:
            out = pl.get("output")
            if isinstance(out, dict):
                out = out.get("content") if isinstance(out.get("content"), str) else out
            rows.append(_out(base, pl.get("call_id"), out))
    rows = finish(rows, session)
    for r in rows:   # an inherited message the rules gave to the app (AGENTS.md, a context block) is no copy of a typed one
        if r.get("copy_of") == -1 and not r["typed_by_developer"]:
            r["copy_of"] = None
    return rows, []


# -- Gemini CLI (one JSON document, or JSON Lines) --------------------------------------------------------------
def _gemini_messages(files):
    """(file, line or None, message) in order, from each part: a JSON document's `messages` (`turns`, where present,
    repeats them: 63 of 63), or JSON Lines with a header line, a line per message and `$set` lines. A message
    written again under its id (323 of 384 in the JSON Lines files of the sample: its tool results added) is read
    once, as last written, where it was first written."""
    out = []
    for name, local in files:
        try:
            doc = _lenient(local.read_bytes())
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            out += [(name, None, m) for m in doc.get("messages") or [] if isinstance(m, dict)]
            continue
        out += [(f, n, e) for f, n, e in _lines([(name, local)]) if isinstance(e.get("type"), str) or "parts" in e]
    return last_writes(out, lambda x: x[2].get("id"))


def last_writes(items: list, key) -> list:
    """Items with a key written more than once: the last write, at the first one's place."""
    first, last = {}, {}
    for i, x in enumerate(items):
        k = key(x)
        if k is not None:
            first.setdefault(k, i)
            last[k] = x
    return [last[key(x)] if key(x) is not None else x for i, x in enumerate(items) if key(x) is None or first[key(x)] == i]


def _gemini_parts(rows: list[dict], base: dict, role: str, parts) -> None:
    """A `{role, parts}` message: text (with images' markers) as one row; a thought, a function call and a function
    response each as its own."""
    texts = []
    for n, p in enumerate(parts if isinstance(parts, list) else []):
        if not isinstance(p, dict):
            continue
        common = {**base, "content_block_index": n}
        if p.get("thought") is True and isinstance(p.get("text"), str):
            rows.append(_think(common, p["text"]))
        elif isinstance(p.get("functionCall"), dict):
            fc = p["functionCall"]
            rows.append(_call(common, fc.get("name"), fc.get("args"), fc.get("id")))
        elif isinstance(p.get("functionResponse"), dict):
            fr = p["functionResponse"]
            rows.append(_out(common, fr.get("id"), fr.get("response"), name=fr.get("name")))
        elif isinstance(p.get("text"), str):
            texts.append(p["text"])
        elif isinstance(p.get("inlineData"), dict):
            texts.append(f"\n[Image: {p['inlineData'].get('mimeType') or 'unknown'}]")
    text = _join(texts)
    if not text.strip():
        return
    if role == "user":
        typed = "<USER_REQUEST>" in text
        rows.append(_user(base, text, typed, "developer" if typed else "harness", "text" if typed else "notice"))
    else:
        rows.append(_say(base, text))


def gemini_cli(files, session):
    rows: list[dict] = []
    for fname, line, m in _gemini_messages(_main(files)):
        base = {"raw_file": fname, "raw_line": line, "timestamp": _iso(m.get("timestamp")),
                "source_entry_uuid": m.get("id") if isinstance(m.get("id"), str) else None}
        kind = m.get("type")
        if kind is None and "parts" in m:   # {role, parts}: the developer's text in <USER_REQUEST>
            _gemini_parts(rows, base, m.get("role"), m.get("parts"))
            continue
        text = _text(m.get("content"))
        if kind == "user":
            if text.strip():
                rows.append(_user(base, text, True, "developer"))
        elif kind == "gemini":
            since, model = len(rows), m.get("model")
            for th in m.get("thoughts") or []:
                if isinstance(th, dict):
                    rows.append(_think({**base, "model": model},
                                       "\n".join(str(x) for x in (th.get("subject"), th.get("description")) if x)))
            if text.strip():
                rows.append(_say({**base, "model": model}, text))
            for c in m.get("toolCalls") or []:
                if not isinstance(c, dict):
                    continue
                rows.append(_call({**base, "model": model}, c.get("name"), c.get("args"), c.get("id")))
                if "result" in c or "resultDisplay" in c:
                    res = c.get("result")
                    rows.append(_out(base, c.get("id"), res if res is not None else c.get("resultDisplay"),
                                     is_error=c.get("status") == "error", name=c.get("name")))
            tok = m.get("tokens") if isinstance(m.get("tokens"), dict) else {}
            _place(rows, _ints(input_tokens=tok.get("input"), output_tokens=tok.get("output"),
                               cache_read_input_tokens=tok.get("cached")), since)
        elif kind in ("info", "error", "warning"):
            rows.append(_meta(base, "system_event", "notice", text or None))
    return finish(rows, session), []


# -- Copilot CLI (event JSONL) ----------------------------------------------------------------------------------
def copilot_cli(files, session):
    """A subagent's events (a top-level `agentId`, or `parentToolCallId`; none in the sample) are its own thread,
    keyed by the call that started it, as Claude Code's subagents are."""
    threads: dict[str | None, list] = {}
    for x in _lines(_main(files)):
        d = x[2].get("data") if isinstance(x[2].get("data"), dict) else {}
        sub = d.get("parentToolCallId") or x[2].get("agentId")
        threads.setdefault(str(sub) if sub else None, []).append(x)
    owner = session.get("checkpoint_pk") or session.get("session_key")
    sub_rows = []
    for sub, xs in sorted((k, v) for k, v in threads.items() if k is not None):
        first = next((x[2] for x in xs), {})
        agent_id = first.get("agentId") or sub
        sub_rows += finish(_copilot_rows(xs), session, {"task_pk": f"{owner}#{sub}", "agent_id": str(agent_id)})
    return finish(_copilot_rows(threads.get(None, [])), session), sub_rows


def _copilot_rows(entries) -> list[dict]:
    rows: list[dict] = []
    for fname, line, e in entries:
        kind = e.get("type") or ""
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        base = {"raw_file": fname, "raw_line": line, "timestamp": _iso(e.get("timestamp")),
                "source_entry_uuid": e.get("id"), "parent_uuid": e.get("parentId")}
        if kind == "user.message":
            text = str(d.get("content") or "")
            if text.strip():
                typed, who = copilot_author(e, d)
                rows.append(_user(base, text, typed, who, "notice" if typed is False else "text"))
        elif kind == "assistant.message":
            since = len(rows)
            common = {**base, "response_id": d.get("messageId"), "model": d.get("model")}
            if str(d.get("reasoningText") or "").strip():
                rows.append(_think(common, d["reasoningText"]))
            if str(d.get("content") or "").strip():
                rows.append(_say(common, d["content"]))
            for n, t in enumerate(d.get("toolRequests") or []):
                if isinstance(t, dict):
                    rows.append(_call({**common, "content_block_index": n}, t.get("name"), t.get("arguments"), t.get("toolCallId")))
            _place(rows, _ints(output_tokens=d.get("outputTokens")), since)
        elif kind == "tool.execution_complete":
            res = d.get("result") if isinstance(d.get("result"), dict) else {}
            rows.append(_out(base, d.get("toolCallId"), res.get("content"), is_error=d.get("success") is False))
        elif kind in ("system.message", "system.notification", "skill.invoked", "abort", "session.error", "session.info"):
            rows.append(_meta(base, "system_event", "notice", d.get("content") if isinstance(d.get("content"), str) else None))
        elif kind.startswith(("hook.", "permission.")):
            rows.append(_meta(base, "progress", "progress"))
        elif kind.startswith("session."):
            rows.append(_meta(base, "system_event", "notice"))
    return rows


# -- Pi and Oh My Pi (JSONL tree of entries) --------------------------------------------------------------------
PI_EVENTS = {"session", "session_info", "model_change", "thinking_level_change", "context_edit", "custom", "title"}


def pi(files, session):
    rows: list[dict] = []
    for fname, line, e in _lines(_main(files)):
        kind = e.get("type")
        base = {"raw_file": fname, "raw_line": line, "timestamp": _iso(e.get("timestamp")),
                "source_entry_uuid": e.get("id"), "parent_uuid": e.get("parentId")}
        if kind == "compaction":
            rows.append(_meta(base, "summary", "compaction", e.get("summary") if isinstance(e.get("summary"), str) else None))
        elif kind == "custom_message":   # an extension's message to the model
            c = e.get("content")
            rows.append(_user(base, c if isinstance(c, str) else _json(c), False, "harness", "notice"))
        elif kind in PI_EVENTS:
            rows.append(_meta(base, "system_event", "notice"))
        if kind != "message":
            continue
        m = e.get("message") if isinstance(e.get("message"), dict) else {}
        role = m.get("role")
        content = m.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content if isinstance(content, str) else ""}]
        base["timestamp"] = _iso(m.get("timestamp")) or base["timestamp"]
        if role == "user":
            texts = []
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "text":
                    texts.append(str(b.get("text") or ""))
                elif isinstance(b, dict) and b.get("type") == "image":
                    texts.append(f"\n[Image: {b.get('mimeType') or 'unknown'}]")
            text = _join(texts)
            if text.strip():
                tag = TAG_OPEN.match(text)
                skill = bool(tag and tag.group(1) == "skill")
                fork = text.lstrip().startswith(PI_FORK)   # a forked subagent's task: its parent agent's
                rows.append(_user(base, text, not (skill or fork), "agent" if fork else "harness" if skill else "developer",
                                  "notice" if (skill or fork) else "text"))
        elif role == "assistant":
            since, model = len(rows), m.get("model")
            for n, b in enumerate(blocks):
                if not isinstance(b, dict):
                    continue
                common = {**base, "response_id": m.get("responseId"), "content_block_index": n, "model": model}
                if b.get("type") == "thinking":
                    rows.append(_think(common, b.get("thinking")))
                elif b.get("type") == "toolCall":
                    rows.append(_call(common, b.get("name"), b.get("arguments"), b.get("id")))
                elif b.get("type") == "text" and str(b.get("text") or "").strip():
                    rows.append(_say(common, b.get("text")))
            u = m.get("usage") if isinstance(m.get("usage"), dict) else {}
            _place(rows, _ints(input_tokens=u.get("input"), output_tokens=u.get("output"),
                               cache_read_input_tokens=u.get("cacheRead"), cache_creation_input_tokens=u.get("cacheWrite")),
                   since)
        elif role == "toolResult":
            rows.append(_out(base, m.get("toolCallId"), _text(blocks), is_error=bool(m.get("isError")), name=m.get("toolName")))
        elif role == "bashExecution":   # the developer ran a command (`!`): their action, not a message to the agent
            rows.append(_user(base, str(m.get("command") or ""), False, "developer", "shell", command=m.get("command")))
            rows.append({**_out(base, None, m.get("output"), is_error=(m.get("exitCode") or 0) != 0), "kind": "shell",
                         "actor": "harness"})
        elif role == "system":
            rows.append(_meta(base, "system_event", "notice", m.get("content") if isinstance(m.get("content"), str) else None))
    return finish(rows, session), []


# -- OpenCode (one JSON document: info, messages with parts) ---------------------------------------------------
def opencode(files, session):
    rows: list[dict] = []
    for fname, doc in [(name, opencode_doc(local.read_bytes())) for name, local in _main(files)]:
        got = doc.get("messages") if isinstance(doc, dict) else None
        for msg in (list(got.values()) if isinstance(got, dict) else list(got or [])):
            if not isinstance(msg, dict):
                continue
            info = msg.get("info") if isinstance(msg.get("info"), dict) else msg
            role = info.get("role")
            t = info.get("time") if isinstance(info.get("time"), dict) else {}
            base = {"raw_file": fname, "timestamp": _iso(t.get("created")), "source_entry_uuid": info.get("id"),
                    "parent_uuid": info.get("parentID")}
            parts = [p for p in msg.get("parts") or [] if isinstance(p, dict)]
            if role == "user":
                typed_texts, first = [], None
                for n, p in enumerate(parts):
                    ptype = p.get("type")
                    if ptype == "text" and p.get("synthetic") and str(p.get("text") or "").strip():
                        rows.append(_user({**base, "content_block_index": n}, p.get("text"), False, "harness", "notice"))
                    elif ptype == "text" and str(p.get("text") or "").strip():
                        typed_texts.append(str(p.get("text")))
                        first = n if first is None else first
                    elif ptype == "file" and str(p.get("mime") or "").startswith("image/"):
                        typed_texts.append(f"\n[Image: {p.get('mime')}]")
                        first = n if first is None else first
                    elif ptype == "compaction":
                        rows.append(_meta({**base, "content_block_index": n}, "summary", "compaction"))
                if _join(typed_texts).strip():
                    rows.append(_user({**base, "content_block_index": first or 0}, _join(typed_texts), True, "developer"))
            elif role == "assistant":
                model, since = info.get("modelID"), len(rows)
                for n, p in enumerate(parts):
                    common = {**base, "response_id": info.get("id"), "content_block_index": n, "model": model}
                    ptype = p.get("type")
                    if ptype == "step-finish":   # one model call's counts, for the rows since the last
                        tok = p.get("tokens") if isinstance(p.get("tokens"), dict) else {}
                        cache = tok.get("cache") if isinstance(tok.get("cache"), dict) else {}
                        _place(rows, _ints(input_tokens=tok.get("input"), output_tokens=tok.get("output"),
                                           cache_read_input_tokens=cache.get("read"),
                                           cache_creation_input_tokens=cache.get("write")), since)
                        since = len(rows)
                    elif ptype == "text" and str(p.get("text") or "").strip():
                        rows.append(_say(common, p.get("text")))
                    elif ptype == "reasoning":
                        rows.append(_think(common, p.get("text")))
                    elif ptype == "tool":
                        state = p.get("state") if isinstance(p.get("state"), dict) else {}
                        rows.append(_call(common, p.get("tool"), state.get("input"), p.get("callID")))
                        if state.get("status") in ("completed", "error"):
                            err = state.get("status") == "error"
                            rows.append(_out({**base, "content_block_index": n}, p.get("callID"),
                                             state.get("error") if err else state.get("output"), is_error=err,
                                             name=p.get("tool")))
                    elif ptype == "patch":
                        rows.append(_meta({**base, "content_block_index": n}, "file_snapshot", "snapshot"))
    return finish(rows, session), []


# -- Anthropic-style messages: Cursor (role JSONL) and Factory Droid (`message` entries) -------------------------
def _blocks(rows: list[dict], base: dict, role: str, content, *, typed=None, who=None, kind="text") -> None:
    """A message's content blocks as rows: an assistant's text, thinking and tool calls, block by block; a user's
    tool results each, and the rest of its text (with image markers) as one row, typed and written by `who` as the
    two functions of its text say."""
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content if isinstance(content, str) else ""}]
    if role == "assistant":
        for n, b in enumerate(blocks):
            if not isinstance(b, dict):
                continue
            common = {**base, "content_block_index": n}
            if b.get("type") == "text" and str(b.get("text") or "").strip():
                rows.append(_say(common, b.get("text")))
            elif b.get("type") in ("thinking", "redacted_thinking"):
                rows.append(_think(common, b.get("thinking")))
            elif b.get("type") == "tool_use":
                rows.append(_call(common, b.get("name"), b.get("input"), b.get("id")))
        return
    texts, first = [], None
    for n, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_result":
            rows.append(_out({**base, "content_block_index": n}, b.get("tool_use_id"), _text(b.get("content")),
                             is_error=bool(b.get("is_error"))))
            continue
        first = n if first is None else first
        if b.get("type") == "text":
            texts.append(str(b.get("text") or ""))
        elif b.get("type") in ("image", "document"):
            src = b.get("source") if isinstance(b.get("source"), dict) else {}
            texts.append(f"\n[{b['type'].capitalize()}: {src.get('media_type') or 'unknown'}]")
    text = _join(texts)
    if text.strip():
        rows.append(_user({**base, "content_block_index": first or 0}, text, typed(text), who(text),
                          kind(text) if callable(kind) else kind))


def cursor(files, session):
    rows: list[dict] = []
    for fname, line, e in _lines(_main(files)):
        base = {"raw_file": fname, "raw_line": line}
        m = e.get("message") if isinstance(e.get("message"), dict) else {}
        if e.get("role") in ("user", "assistant"):
            _blocks(rows, base, e["role"], m.get("content"), typed=lambda t: cursor_author(t)[0],
                    who=lambda t: cursor_author(t)[1], kind=lambda t: cursor_author(t)[2])
        elif e.get("type") == "turn_ended":
            rows.append(_meta(base, "system_event", "notice"))
    return finish(rows, session), []


def factory_droid(files, session):
    rows: list[dict] = []
    for fname, line, e in _lines(_main(files)):
        kind = e.get("type")
        base = {"raw_file": fname, "raw_line": line, "timestamp": _iso(e.get("timestamp")),
                "source_entry_uuid": e.get("id"), "parent_uuid": e.get("parentId")}
        if kind == "compaction_state":
            rows.append(_meta(base, "summary", "compaction", e.get("summaryText") if isinstance(e.get("summaryText"), str) else None))
            continue
        if kind in ("session_start", "todo_state", "agent_turn_outcome"):
            rows.append(_meta(base, "system_event", "notice"))
            continue
        if kind != "message":
            continue
        m = e.get("message") if isinstance(e.get("message"), dict) else {}
        if m.get("role") == "user" and m.get("hookEventName"):   # a hook ran: its output, shown to the user only
            rows.append(_meta(base, "progress", "progress", _json(m.get("hookResults")) if m.get("hookResults") else None))
        elif m.get("role") == "user":
            marked = m.get("visibility") in ("llm_only", "user_only")   # a reminder for the model, a notice for the user
            # Nothing marks text Droid inserts: the developer's, unless the text shows a harness's (events.tell;
            # 437 rows had no author before 2026-10-05)
            _blocks(rows, base, "user", m.get("content"), typed=lambda t: False if marked else tell(t)[1],
                    who=lambda t: "harness" if marked else tell(t)[2], kind=lambda t: "notice" if marked else tell(t)[3])
        elif m.get("role") == "assistant":
            _blocks(rows, {**base, "response_id": e.get("id"), "model": m.get("modelId") or m.get("model")}, "assistant",
                    m.get("content"))
    return finish(rows, session), []


# -- Devin (JSONL, one line per message with a role: user, assistant, tool) --------------------------------------
def devin(files, session):
    rows: list[dict] = []
    for fname, line, e in _lines(_main(files)):
        role = e.get("role")
        md = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
        base = {"raw_file": fname, "raw_line": line, "timestamp": _iso(e.get("timestamp")),
                "source_entry_uuid": e.get("message_id")}
        if role == "user":
            text = _text(e.get("content"))
            if text.strip():
                rows.append(_prompt_row(base, text))
        elif role == "assistant":
            since = len(rows)
            common = {**base, "response_id": e.get("message_id"), "model": md.get("generation_model")}
            th = e.get("thinking") if isinstance(e.get("thinking"), dict) else {}
            if str(th.get("thinking") or "").strip():
                rows.append(_think(common, th["thinking"]))
            if _text(e.get("content")).strip():
                rows.append(_say(common, _text(e.get("content"))))
            for n, c in enumerate(e.get("tool_calls") or []):
                if isinstance(c, dict):
                    rows.append(_call({**common, "content_block_index": n}, c.get("name"), c.get("arguments"), c.get("id")))
            mt = md.get("metrics") if isinstance(md.get("metrics"), dict) else {}
            _place(rows, _ints(input_tokens=mt.get("input_tokens"), output_tokens=mt.get("output_tokens"),
                               cache_read_input_tokens=mt.get("cache_read_tokens"),
                               cache_creation_input_tokens=mt.get("cache_creation_tokens")), since)
        elif role == "tool":
            rows.append(_out(base, e.get("tool_call_id"), _text(e.get("content"))))
    return finish(rows, session), []


# -- Entire's hook log: Grok CLI, Qwen Code (JSONL, one line per hook event) -----------------------------------------
# Entire records these agents by their hooks (`v` 1), not by their transcripts. Each line is one hook event:
# - UserPromptSubmit: the prompt submitted;
# - PreToolUse and PostToolUse: a tool call before and after it runs, paired by `tool_use_id`. In the collection 13
#   of 9,699 calls got no result;
# - Stop and StopFailure: the end of a turn, with the agent's last message.
# The agent's text between its calls is not in the log. A turn's rows are its prompt, its calls and their results, and
# that last message. A submitted prompt is the developer's, typed. Grok CLI wraps it in `<user_query>`, which finish
# takes off; it also submits its own reminders as prompts, which finish marks. A prompt that opens `/name` runs a
# command or a skill. An event that carries `agent_type` is a subagent's, and its prompt is its parent agent's. Entire
# records a subagent's run as a session of its own, every event carrying `agent_type` (13 of the 36 Grok CLI sessions
# in the collection, holding every subagent event in it): those rows are the session's own. A parent's log marks each
# subagent's start (SubagentStart, a notice of its own). A subagent's events inside its parent's log (none in the
# collection) go to conversations_subagents, one thread per type (`{owner}#{agent_type}`), so two subagents of one
# type that run at once would share a thread.
HOOK_FAILED = {"FileNotFound", "NotFound", "IsADirectory", "InvalidInput", "TaskNotFound", "MultipleMatchesFound"}
SLASH = re.compile(r"\s*/[A-Za-z][\w.:-]*(?:\s|$)")


def _hook_failed(response) -> bool | None:
    """Whether a tool's response says it failed: its own flag, a command's exit code, or one of Grok CLI's failure
    kinds (a response holds its kind as a key: `{"type": ..., "FileNotFound": ...}`); None when it does not say."""
    if not isinstance(response, dict):
        return None
    if isinstance(response.get("is_error"), bool):
        return response["is_error"]
    code = response.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool):
        return code != 0
    return True if HOOK_FAILED & set(response) or response.get("type") in HOOK_FAILED else None


def hooks(files, session):
    events = list(_lines(_main(files)))
    own = bool(events) and all(e.get("agent_type") for _, _, e in events)   # a subagent's run, a session of its own
    main: list[dict] = []
    threads: dict[str, list[dict]] = defaultdict(list)
    model = None
    for fname, line, e in events:
        event, sub = e.get("event"), e.get("agent_type")
        rows = threads[str(sub)] if sub and not own and event != "SubagentStart" else main
        base = {"raw_file": fname, "raw_line": line, "timestamp": _iso(e.get("ts"))}
        model = e.get("model") if isinstance(e.get("model"), str) else model
        if event == "UserPromptSubmit":
            text = e.get("prompt") if isinstance(e.get("prompt"), str) else ""
            if not text.strip():
                continue
            if sub:
                rows.append(_user(base, text, False, "agent", "notice"))
            elif SLASH.match(text):
                rows.append(_user(base, text, True, "developer", "command", command=text.strip()))
            else:
                rows.append(_user(base, text, True, "developer"))
        elif event == "PreToolUse":
            rows.append(_call({**base, "model": model}, e.get("tool_name"), e.get("tool_input"), e.get("tool_use_id")))
        elif event == "PostToolUse":
            rows.append(_out(base, e.get("tool_use_id"), e.get("tool_response"),
                             is_error=_hook_failed(e.get("tool_response")), name=e.get("tool_name")))
        elif event in ("Stop", "StopFailure"):
            said = e.get("last_assistant_message")
            if event == "StopFailure":
                rows.append(_meta(base, "system_event", "notice", e.get("error") if isinstance(e.get("error"), str) else None,
                                  is_error=True))
            if isinstance(said, str) and said.strip():
                rows.append(_say({**base, "model": model}, said))
            elif event == "Stop":
                rows.append(_meta(base, "system_event", "notice"))
        elif event == "PreCompact":
            rows.append(_meta(base, "summary", "compaction"))
        elif event:   # the session's start and end, a subagent's start, a stop cancelled, the agent's notifications
            rows.append(_meta(base, "system_event", "notice", e.get("message") if isinstance(e.get("message"), str) else None))
    owner = session.get("checkpoint_pk") or session.get("session_key")
    sub_rows: list[dict] = []
    for kind, rows in sorted(threads.items()):
        sub_rows += finish(rows, session, {"task_pk": f"{owner}#{kind}", "agent_id": kind})
    return finish(main, session), sub_rows


# -- Kiro (one JSON document: a conversation's history of turns) ---------------------------------------------------
# Each turn pairs a user's content with the assistant's: `Prompt` (what was submitted) with `Response` (the agent's
# text) or `ToolUse` (its calls). The file keeps no tool results and no times: in the collection the 6 user turns that
# follow a call have no content. Nothing marks who wrote a prompt (_prompt_row).
def kiro(files, session):
    rows: list[dict] = []
    for fname, doc in _docs(_main(files)):
        history = doc.get("history") if isinstance(doc, dict) else None
        for turn in history if isinstance(history, list) else []:
            if not isinstance(turn, dict):
                continue
            user = turn.get("user") if isinstance(turn.get("user"), dict) else {}
            content = user.get("content") if isinstance(user.get("content"), dict) else {}
            prompt = content.get("Prompt") if isinstance(content.get("Prompt"), dict) else {}
            if isinstance(prompt.get("prompt"), str) and prompt["prompt"].strip():
                rows.append(_prompt_row({"raw_file": fname}, prompt["prompt"]))
            reply = turn.get("assistant") if isinstance(turn.get("assistant"), dict) else {}
            for part in ("Response", "ToolUse"):
                a = reply.get(part) if isinstance(reply.get(part), dict) else None
                if a is None:
                    continue
                common = {"raw_file": fname, "response_id": a.get("message_id"), "source_entry_uuid": a.get("message_id")}
                if isinstance(a.get("content"), str) and a["content"].strip():
                    rows.append(_say(common, a["content"]))
                for n, c in enumerate(a.get("tool_uses") or []):
                    if isinstance(c, dict):
                        rows.append(_call({**common, "content_block_index": n}, c.get("name"), c.get("args"), c.get("id")))
    return finish(rows, session), []


# -- Roger Roger Agent (JSONL: one message per line, a user's or the assistant's) ------------------------------------
# A message is text, or Anthropic-style text blocks (6 of 24 lines in the collection). Nothing marks who wrote a user
# message (_prompt_row).
def roger_roger(files, session):
    rows: list[dict] = []
    for fname, line, e in _lines(_main(files)):
        m = e.get("message")
        text = m if isinstance(m, str) else _text(m.get("content")) if isinstance(m, dict) else ""
        base = {"raw_file": fname, "raw_line": line, "timestamp": _iso(e.get("timestamp"))}
        if e.get("type") == "user" and text.strip():
            rows.append(_prompt_row(base, text))
        elif e.get("type") == "assistant" and text.strip():
            rows.append(_say(base, text))
    return finish(rows, session), []


# -- Antigravity (JSONL: one line per step of the agent's trajectory) -----------------------------------------------
# A step's `source` says who made it:
# - USER_EXPLICIT: the developer's input, typed. Antigravity puts what they typed in `<USER_REQUEST>` and adds its
#   own blocks after it (`<ADDITIONAL_METADATA>`, `<USER_SETTINGS_CHANGE>`, `<PLAN>`); the row keeps the request.
# - MODEL: the planner's responses (its `thinking`, its text in `content`, its `tool_calls`) and the tools they ran.
#   Each tool that ran is a step of its own after the response (VIEW_FILE, RUN_COMMAND, CODE_ACTION, GENERIC, ...),
#   which names no call, so steps are paired with the waiting calls in order. In the collection 7 calls got no step
#   and 5 steps found no call waiting; those have no call id. A step still RUNNING when the file was written holds
#   the output so far.
# - SYSTEM: the harness's messages to the model, its context, and its checkpoints (a compaction's summary).
def _request(text: str) -> str:
    """What the developer typed in an Antigravity input: its `<USER_REQUEST>` (to the end when it is not closed), else
    the whole text."""
    i = text.find("<USER_REQUEST>")
    if i < 0:
        return text.strip()
    j = text.find("</USER_REQUEST>", i)
    return text[i + len("<USER_REQUEST>"): j if j >= 0 else len(text)].strip()


def antigravity(files, session):
    rows: list[dict] = []
    waiting: deque = deque()
    for fname, line, e in _lines(_main(files)):
        kind, source = e.get("type"), e.get("source")
        content = e.get("content") if isinstance(e.get("content"), str) else None
        base = {"raw_file": fname, "raw_line": line, "timestamp": _iso(e.get("created_at")),
                "source_entry_uuid": str(e["step_index"]) if e.get("step_index") is not None else None}
        if source == "USER_EXPLICIT":
            said = _request(content or "")
            if said:
                rows.append(_user(base, said, True, "developer"))
            elif content and content.strip():
                rows.append(_user(base, content, False, "harness", "notice"))
        elif kind == "PLANNER_RESPONSE":
            waiting.clear()   # a call whose step never came gets no result
            if isinstance(e.get("thinking"), str) and e["thinking"].strip():
                rows.append(_think(base, e["thinking"]))
            if content and content.strip():
                rows.append(_say(base, content))
            for n, c in enumerate(e.get("tool_calls") or []):
                if isinstance(c, dict):
                    cid = f"{e.get('step_index')}.{n}"
                    rows.append(_call({**base, "content_block_index": n}, c.get("name"), c.get("args"), cid))
                    waiting.append(cid)
        elif source == "MODEL":   # a tool the planner ran
            code = e.get("exit_code")
            failed = True if e.get("status") == "ERROR" or e.get("error") else (
                code != 0 if isinstance(code, int) and not isinstance(code, bool) else None)
            rows.append(_out(base, waiting.popleft() if waiting else None, content, is_error=failed))
        elif kind == "CHECKPOINT":
            rows.append(_meta(base, "summary", "compaction", content))
        elif kind == "SYSTEM_MESSAGE":
            rows.append(_user(base, content or "", False, "harness", "notice"))
        else:   # the harness's context (CONVERSATION_HISTORY, KNOWLEDGE_ARTIFACTS), an error message
            rows.append(_meta(base, "system_event", "notice", content, **({"is_error": True} if kind == "ERROR_MESSAGE" else {})))
    return finish(rows, session), []


# -- Project Runner (SWE-chat v2: JSONL, one delegated run) -------------------------------------------------------
# A program hands one task to another agent and records the run:
# - `session`: how it ran (`entrypoint` delegate or bridge; `harness` cursor-headless, agy, grok or hermes);
# - `user`: the task sent;
# - `assistant`: the agent's reply;
# - `file`: a file it wrote;
# - `terminal`: how it ended (`outcome`, `returncode`).
# A delegated task is the delegating agent's, not typed: 389 of the 396 tasks open `[delegate worktree]`. Any other
# entrypoint's task goes through _prompt_row.
def project_runner(files, session):
    rows: list[dict] = []
    entrypoint = None
    for fname, line, e in _lines(_main(files)):
        kind = e.get("type")
        md = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
        entrypoint = md.get("entrypoint") or entrypoint
        base = {"raw_file": fname, "raw_line": line, "timestamp": _iso(e.get("timestamp"))}
        text = e.get("text") if isinstance(e.get("text"), str) else ""
        if kind == "user" and text.strip():
            rows.append(_user(base, text, False, "agent", "notice") if entrypoint == "delegate" else _prompt_row(base, text))
        elif kind == "assistant" and text.strip():
            rows.append(_say({**base, "model": e.get("model") or md.get("actual_model") or None}, text))
        elif kind == "file":
            rows.append(_meta(base, "file_snapshot", "snapshot", file_path=e.get("path") if isinstance(e.get("path"), str) else None))
        elif kind == "terminal":
            rows.append(_meta(base, "system_event", "notice", md.get("outcome"),
                              is_error=md.get("outcome") != "ok" if md.get("outcome") else None))
        elif kind:
            rows.append(_meta(base, "system_event", "notice"))
    return finish(rows, session), []


# -- Kimi Code (SWE-chat v2: JSONL, the agent's event log) ---------------------------------------------------------
# Every message the model saw is a `context.append_message`, and each step of the agent's loop is a
# `context.append_loop_event`: a part of its reply (`think` or `text`), a tool call, a tool result, and the step's end
# with its token counts. Who wrote a user message is its `origin`:
# - `user` is the developer's, typed;
# - `injection` (a `<system-reminder>`) and `task` (a background task's `<notification>`) are the harness's.
# A `turn.prompt` repeats a prompt that is also appended as a message (70 of 70 in the collection), so it adds no
# row. Model calls and token records (`llm.request`, `usage.record`) are read for the model only. Compaction keeps its
# summary.
KIMI_SKIPPED = {"llm.request", "usage.record", "turn.prompt", "llm.tools_snapshot", "tools.set_active_tools",
                "mcp.tools_discovered"}


def kimi_code(files, session):
    rows: list[dict] = []
    model, since = None, 0
    for fname, line, e in _lines(_main(files)):
        kind = e.get("type")
        base = {"raw_file": fname, "raw_line": line, "timestamp": _iso(e.get("time"))}
        if kind == "llm.request":
            model = e.get("model") if isinstance(e.get("model"), str) else model
        if kind == "context.append_message":
            m = e.get("message") if isinstance(e.get("message"), dict) else {}
            text = _text(m.get("content"))
            origin = (m.get("origin") or {}).get("kind") if isinstance(m.get("origin"), dict) else None
            if m.get("role") == "user" and text.strip():   # the agent's own messages are its loop events
                rows.append(_user(base, text, True, "developer") if origin == "user" else
                            _user(base, text, False, "harness", "notice"))
        elif kind == "context.append_loop_event":
            ev = e.get("event") if isinstance(e.get("event"), dict) else {}
            step = ev.get("type")
            if step == "step.begin":
                since = len(rows)
            elif step == "content.part":
                part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
                if part.get("type") == "think":
                    rows.append(_think({**base, "model": model}, part.get("think")))
                elif part.get("type") == "text" and str(part.get("text") or "").strip():
                    rows.append(_say({**base, "model": model}, part.get("text")))
            elif step == "tool.call":
                rows.append(_call({**base, "model": model}, ev.get("name"), ev.get("args"), ev.get("toolCallId")))
            elif step == "tool.result":
                res = ev.get("result") if isinstance(ev.get("result"), dict) else {}
                rows.append(_out(base, ev.get("toolCallId"), res.get("output"),
                                 is_error=res["isError"] if isinstance(res.get("isError"), bool) else None))
            elif step == "step.end":
                for r in rows[since:]:
                    if r["role"] in ("assistant", "tool_use"):
                        r["response_id"] = ev.get("messageId")
                u = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
                _place(rows, _ints(input_tokens=u.get("inputOther"), output_tokens=u.get("output"),
                                   cache_read_input_tokens=u.get("inputCacheRead"),
                                   cache_creation_input_tokens=u.get("inputCacheCreation")), since)
                since = len(rows)
        elif kind == "context.apply_compaction":
            rows.append(_meta(base, "summary", "compaction", e.get("summary") if isinstance(e.get("summary"), str) else None))
        elif kind == "turn.ended" and e.get("error"):
            err = e["error"] if isinstance(e["error"], dict) else {}
            rows.append(_meta(base, "system_event", "notice", err.get("message") if isinstance(err.get("message"), str) else None,
                              is_error=True))
        elif kind and kind not in KIMI_SKIPPED:   # the session's settings, permission and plan modes, background tasks
            rows.append(_meta(base, "system_event", "notice"))
    return finish(rows, session), []


EXTRACTORS = {"codex": codex, "gemini cli": gemini_cli, "copilot cli": copilot_cli, "pi": pi, "oh my pi": pi,
              "opencode": opencode, "cursor": cursor, "devin": devin, "factory ai droid": factory_droid,
              "factory droid": factory_droid, "grok cli": hooks, "qwen code": hooks, "kiro": kiro,
              "roger roger agent": roger_roger, "antigravity": antigravity, "project runner": project_runner,
              "kimi code": kimi_code}


# -- SpecStory (markdown) -----------------------------------------------------------------------------------------
# Blocks open `_**User ...**_` or `_**Agent (<model> <time>)**_` (`Assistant` in older files). SpecStory renders each
# tool call, its input and its output together, as a `<tool-use data-tool-type=... data-tool-name=...>` block (147,736
# in the sample's 785 files): one tool_use row holds it whole, and no tool_result row is made from a rendering.
# `<think>` blocks are the agent's thinking. A user block is what SpecStory rendered of the prompt, and nothing marks
# text the tool inserted: who typed it is unknown.
MD_HEAD = re.compile(r"^_\*\*(User|Agent|Assistant)\b([^*]*)\*\*_\s*$", re.M)
MD_PIECE = re.compile(r"<tool-use\b([^>]*)>(.*?)</tool-use>|<think\b[^>]*>(.*?)</think>", re.S)
MD_WRAP = re.compile(r"^\s*<details>\s*<summary>.*?</summary>|</details>\s*$", re.S)
MD_TIME = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)Z")


# A shell call's command, from how SpecStory rendered it (read 2026-10-05 over 4,594 files' shell renderings, 130,000
# blocks). It is found, in this order:
# - the summary's backticked text after the tool's name, `Tool use: **exec_command** `git status``: Codex's, Pi's and
#   Cursor CLI's;
# - the body's `Parameters:` JSON (VS Code Copilot's), or its `- command:` line under `**Input:**` (Codex's `shell`,
#   OpenCode's, Copilot's);
# - a fence marked as shell, ```bash and the like: Gemini CLI's, Cursor's, Pi's, some of Claude Code's;
# - the summary's text after `Run command:` (Cursor's, when no fence holds it);
# - a line that is one backticked span before the output's fence: Claude Code's Bash, Antigravity's.
# A ```text or bare fence holds the output, never the command. None where no shape holds.
MD_SHELL_FENCE = re.compile(r"^```(?:bash|sh|shell|zsh|console|powershell|pwsh|ps1)[ \t]*\n(.*?)\n```", re.M | re.S)
MD_JSON_FENCE = re.compile(r"^```json[ \t]*\n(.*?)\n```", re.M | re.S)
MD_TICKED = re.compile(r"`([^`\n]+)`")


def _md_listed(value: str) -> str | None:
    """An Input list's command as written: backticks around it taken off, and Codex's argument list (`[bash -lc ls]`,
    or a JSON list) read as the command line it runs."""
    if len(value) > 1 and value[0] == value[-1] == "`":
        value = value[1:-1].strip()
    if value.startswith("["):
        try:
            got = json.loads(value)
        except ValueError:
            got = None
        if isinstance(got, list) and got and all(isinstance(x, str) for x in got):
            return " ".join(got).strip() or None
        if value.endswith("]") and re.match(r"\[(?:\S*/)?(?:bash|sh|zsh|dash)\s", value):
            value = value[1:-1].strip()
    return value or None


def _md_command(block: str) -> str | None:
    import html

    i = block.find("<summary>")   # found, not matched: a lazy pattern tried from each unclosed tag is quadratic
    j = block.find("</summary>", i + 9) if i >= 0 else -1
    summary = html.unescape(block[i + 9:j]).strip() if j >= 0 else ""
    body = block[j + 10:] if j >= 0 else block
    body = body.rstrip().removesuffix("</details>")   # the rendering's own close, never the command's
    named = re.match(r"Tool use: \*\*[^*\n]*\*\*[ \t]*(.*)", summary, re.S)
    rest = named[1].strip() if named else ""
    if len(rest) > 2 and rest[0] == "`" and rest[-1] == "`":
        return rest[1:-1].strip() or None
    if "Parameters:" in body:
        got = MD_JSON_FENCE.search(body)
        try:
            value = json.loads(got[1]) if got else None
        except ValueError:
            value = None
        if isinstance(value, dict):   # `command`, or VS Code's `commandLine`: {original, toolEdited, userEdited}
            said = value.get("command") if isinstance(value.get("command"), str) else value.get("commandLine")
            if isinstance(said, dict):   # what ran: the developer's edit, else the tool's, else the original
                said = next((said[k] for k in ("userEdited", "toolEdited", "original")
                             if isinstance(said.get(k), str) and said[k].strip()), None)
            if isinstance(said, str) and said.strip():
                return said.strip()
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("- command:"):
            said = [line[len("- command:"):].strip()]
            for more in lines[i + 1:]:
                if not more.strip() or more.startswith(("- ", "```", "**")):
                    break
                said.append(more)
            return _md_listed("\n".join(said).strip())
    fence = MD_SHELL_FENCE.search(body)
    if fence:
        return fence[1].strip() or None
    if "Run command:" in rest:
        return rest.split("Run command:", 1)[1].strip() or None
    first = body.find("```")
    for line in (body[:first] if first >= 0 else body).splitlines():
        ticked = MD_TICKED.fullmatch(line.strip())
        if ticked:
            return ticked[1].strip() or None
    return None


def _md_summary(block: str) -> tuple[str, str, str]:
    """A rendering's text before its summary, its summary (read as text) and the body after it, found by plain search
    (a lazy pattern tried from each unclosed tag is quadratic, as _md_command found)."""
    i = block.find("<summary>")
    j = block.find("</summary>", i + 9) if i >= 0 else -1
    if j < 0:
        return "", "", block
    before = re.sub(r"<details\b[^>]*>\s*$", "", block[:i].strip()).strip()
    return before, html.unescape(block[i + 9:j]).strip(), block[j + 10:]


def _md_rendering(block: str) -> str:
    """A tool call's rendering as its row's content: what its summary says the call did (after `Tool use: **name**`,
    which tool_name holds) and the body under it, the HTML wrapper off. 112,079 calls said what they read or edited
    in the summary alone (`Read file: src/a.ts`), and had empty content before 2026-10-05."""
    before, summary, body = _md_summary(block)
    said = re.sub(r"^Tool use: \*\*[^*\n]*\*\*[ \t]*(?:•[ \t]*)?", "", summary).strip()
    return "\n\n".join(x for x in (before, said, MD_WRAP.sub("", body).strip()) if x)


# A tool call outside any <tool-use> block, as one of the pieces SpecStory separates by `---` lines (read over the
# collection's 15,021 files, 2026-10-05):
# - `Tool use: **name**` and the call's rendering under it (a `<details>` block, a `Read file:` line, a command's
#   fences): an intermediate version's, in both `_**Assistant**_` and `_**Agent**_` blocks (130,221 pieces), its tool
#   named;
# - in older files' `_**Assistant**_` blocks (4,790 files), a `<details>` block whose summary says what the call did
#   (Edit file, Searched codebase, Grep search for, Listed directory: 80,682) or a `Read file: <path>` line (67,001):
#   no tool named, as the file names none. In `_**Agent**_` blocks such a piece is the agent's own text or a tool's
#   output quoting SpecStory's markup (all 20 found there), as is a `<tool-use>` tag never closed.
# A piece of anything else is the agent's text. `---` lines around a text, and the empty `_****_` mark some versions
# open a piece with, are SpecStory's, not the agent's.
MD_LEAD = re.compile(r"Tool use: \*\*([^*\n]+)\*\*")
MD_MARK = "_****_"


def _md_call(piece: str, older: bool) -> tuple[str, str | None, str] | None:
    """A piece that is one tool call: its content (_md_rendering), its tool's name (None where the file names none)
    and the rendering itself; else None. `older`: the piece is in an `_**Assistant**_` block."""
    p = piece.strip()
    lead = MD_LEAD.match(p)
    if lead:
        rest = p[lead.end():].strip()
        return _md_rendering(rest) if rest.startswith("<details") else rest, lead[1].strip(), rest
    if not older:
        return None
    if p.startswith("Read file: ") and "\n" not in p:
        return p, None, p
    if (p.startswith("<details") and p.endswith("</details>") and p.count("<details") == 1
            and p.count("</details>") == 1 and "<summary>" in p):
        return _md_rendering(p), None, p
    return None


def _md_fields(name: str | None, rendering: str) -> dict:
    """A named call's input fields: by the readers' rule (tool_fields) where the rendering is its input's JSON (the
    intermediate version's Claude Code calls, `{"file_path": ...}`); else a shell call's command from the rendering
    (_md_command). Nothing for a call with no name."""
    if not name:
        return {}
    got = None
    if rendering.startswith("{") and rendering.endswith("}"):
        try:
            got = json.loads(rendering)
        except ValueError:
            got = None
    if isinstance(got, dict):
        return {"tool_input_json": _json(got), **tool_fields(name, got)}
    return {"command": _md_command(rendering)} if name.lower() in SHELL_TOOLS else {}


def _md_trim(text: str) -> str:
    """Text with the `---` lines and blank lines around it off (found, then sliced: popping from the front of a list
    is quadratic in the lines taken off, #17's review)."""
    lines = text.split("\n")
    kept = [i for i, x in enumerate(lines) if x.strip() not in ("", "---")]
    return "\n".join(lines[kept[0]:kept[-1] + 1]).strip() if kept else ""


def _md_said(rows: list, base: dict, n: int, said: str, older: bool) -> int:
    """The agent's blocks between two <tool-use> or <think> blocks: each `---` piece that is a tool call a tool_use
    row (a call's `Tool use:` line opens a piece of its own), and the text around them rows of their own. The next
    block index. `older`: an `_**Assistant**_` block's."""
    lines = [x for x in said.split("\n") if x.strip() != MD_MARK]
    pieces, start = [], 0   # (first line, end, where the next piece starts)
    for k, line in enumerate(lines + ["---"]):
        if line.strip() == "---":
            pieces.append((start, k, k + 1))
            start = k + 1
        elif MD_LEAD.match(line) and any(x.strip() for x in lines[start:k]):
            pieces.append((start, k, k))
            start = k
    calls = [(a, nxt, c) for a, b, nxt in pieces if (c := _md_call("\n".join(lines[a:b]), older)) is not None]
    at = 0
    for a, nxt, call in calls + [(len(lines), len(lines), None)]:
        text = _md_trim("\n".join(lines[at:a]))
        if text:
            rows.append(_say({**base, "content_block_index": n}, text))
            n += 1
        if call is not None:
            content, name, rendering = call
            rows.append(row(**base, content_block_index=n, role="tool_use", turn_type="tool_use", content=content,
                            tool_name=name, kind="tool_call", actor="agent", typed_by_developer=False,
                            **_md_fields(name, rendering)))
            n += 1
        at = nxt
    return n


def _md_lines(text: str):
    """A function giving the line of a position, from the text's line starts found once (counting from the top for
    each block took 254 s on a 64 MB history)."""
    import bisect

    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    return lambda at: bisect.bisect_right(starts, at)


def specstory(files, session):
    name, local = files[0]
    text = local.read_text(encoding="utf-8", errors="replace")
    if LSL_FILE.match(text):
        if not session.get("agent"):   # the logging tool's files are Claude Code's sessions: their title and their tools
            session["agent"] = "Claude Code"
        return finish(lsl(name, text), session), []
    heads = list(MD_HEAD.finditer(text))
    line_of = _md_lines(text)
    rows: list[dict] = []
    for i, h in enumerate(heads):
        start, end = h.end(), heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[start:end]
        t = MD_TIME.search(h[2])
        base = {"raw_file": name, "raw_line": line_of(h.start()),
                "timestamp": _iso(f"{t[1]}T{t[2]}Z") if t else None}
        if h[1] == "User":
            if body.strip():
                rows.append(_prompt_row(base, body.strip().removesuffix("---").strip()))
            continue
        m = re.search(r"\(([^)\s/]+)", h[2])
        base["model"] = m[1] if m and not re.match(r"\d", m[1]) else None
        last, n = 0, 0
        for piece in MD_PIECE.finditer(body):
            n = _md_said(rows, base, n, body[last:piece.start()], h[1] == "Assistant")
            if piece[2] is not None:
                tool = re.search(r'data-tool-name="([^"]*)"', piece[1] or "")
                shell = bool(tool) and tool[1].lower() in SHELL_TOOLS
                rows.append(row(**base, content_block_index=n, role="tool_use", turn_type="tool_use",
                                content=_md_rendering(piece[2]), tool_name=tool[1] if tool else None,
                                kind="tool_call", actor="agent", typed_by_developer=False,
                                command=_md_command(piece[2]) if shell else None))
            else:
                rows.append(_think({**base, "content_block_index": n}, MD_WRAP.sub("", piece[3]).strip()))
            n += 1
            last = piece.end()
        _md_said(rows, base, n, body[last:], h[1] == "Assistant")
    return finish(rows, session), []


# -- Live Session Logging's hourly files (markdown, in `.specstory/history/` beside SpecStory's) ----------------------
# A session-logging tool for Claude Code (its titles and tools are Claude Code's) writes one file an hour. Of the
# 1,239 such files in the collection, 1,200 use one of its two layouts; the rest are notes, not sessions, and give no
# rows.
# - `# WORK SESSION (HHMM-HHMM)` (497 files). Each `## Prompt Set` holds `### <Tool> - <time> UTC` entries. Each entry
#   is one tool call: `**Tool:**`, `**Input:**` (JSON), `**Result:**` (✅ or ❌) and `**Output:**`. It sits under the
#   prompt it served, `**User Request:**`, which repeats on every call the prompt led to, so it gets one row where it
#   changes. A call with no prompt is marked `**System Action:** (Initiated automatically)`. `### Text Exchange`
#   entries hold a `**User Message:**` and an `**Assistant Response:**`.
# - `# Claude Code Session Log` (703 files). Each prompt set holds `### User` (the prompt, in markdown of its own) and
#   `### Assistant` entries. An assistant entry is the agent's text, or a tool call in the same `**Tool:**` form. These
#   files have Text Exchange entries too.
# In the second layout a `### User` entry opening `**Result:** ✅` (or ❌) is a tool's result, the output under it
# (34,660 of the collection's 37,116 User entries, 2026-10-05; read as prompts before): each is its call's, in the
# order the calls were made (Claude Code returns parallel calls' results in order; 28,041 follow a call directly).
# Nothing marks text the tool inserted in a prompt (_prompt_row). An output is fenced. A fence inside it (a file's own
# code block) is kept, and the output ends at the entry's last fence. The tool records no call ids. A heading the
# tool's own log prints inside an output (`cat` of an earlier log) starts an entry, as it would in any reading of the
# file.
LSL_FILE = re.compile(r"\s*# (?:WORK SESSION \(\d{4}-\d{4}\)|Claude Code Session Log\b)")
LSL_SET = re.compile(r"## Prompt Set\b[^\n]*\(ps_\d+\)\s*$")
LSL_ENTRY = re.compile(r"### (?:(User|Assistant)\s*$|(.+?) - (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) UTC\b)")
LSL_FILLER = re.compile(r"\s*$|---\s*$|<a name=\"ps_\d+\"></a>\s*$")
LSL_CALL_LABELS = ("**Tool:**", "**Input:**", "**Result:**", "**Output:**")


def _lsl_call(body: list[str]) -> dict:
    """An entry's tool call: the prompt it served, its tool, its input, whether it failed, and its output."""
    got = {"request": None, "tool": None, "input": None, "failed": None, "output": None}
    stop = next((i for i, x in enumerate(body) if x.startswith(LSL_CALL_LABELS)), len(body))
    for i, x in enumerate(body[:stop]):
        if x.startswith("**User Request:**"):
            got["request"] = "\n".join([x[len("**User Request:**"):]] + body[i + 1:stop]).strip()
            break
    i = stop
    while i < len(body):
        x = body[i]
        if x.startswith("**Tool:**"):
            got["tool"] = x[len("**Tool:**"):].strip() or None
        elif x.startswith("**Input:**"):   # JSON, one value a line: its fence closes at the first bare fence
            j = next((k for k in range(i + 1, len(body)) if body[k].rstrip() == "```"), len(body))
            got["input"] = "\n".join(body[i + 1:j])
            i = j + 1
            continue
        elif x.startswith("**Result:**"):
            said = x[len("**Result:**"):].strip()
            got["failed"] = True if said.startswith("❌") else False if said.startswith("✅") else None
        elif x.startswith("**Output:**"):
            first = x[len("**Output:**"):].strip()
            if first.startswith("```"):
                ends = [k for k in range(i + 1, len(body)) if body[k].rstrip() == "```"]
                got["output"] = "\n".join(body[i + 1:ends[-1] if ends else len(body)])
            else:
                got["output"] = "\n".join([first] + body[i + 1:]).strip()
            break
        i += 1
    return got


def _lsl_result(body: list[str]) -> tuple[bool | None, str] | None:
    """A User entry that is a tool's result: (whether it failed, its output); else None."""
    at = next((i for i, x in enumerate(body) if x.strip()), None)
    if at is None or not body[at].lstrip().startswith("**Result:**"):
        return None
    said = body[at].lstrip()[len("**Result:**"):].strip()
    return True if said.startswith("❌") else False if said.startswith("✅") else None, "\n".join(body[at + 1:]).strip()


def _lsl_exchange(body: list[str]) -> tuple[str | None, str | None]:
    """A Text Exchange's user message and assistant response; a `**Type:**` line closes it."""
    text = "\n".join(body[:-1] if body and body[-1].startswith("**Type:**") else body)
    user_at, said_at = text.find("**User Message:**"), text.find("**Assistant Response:**")
    user = said = None
    if user_at >= 0:
        user = text[user_at + len("**User Message:**"):said_at if said_at > user_at else len(text)].strip()
    if said_at >= 0:
        said = text[said_at + len("**Assistant Response:**"):].strip()
    return user, said


def lsl(name: str, text: str) -> list[dict]:
    blocks: list[list] = []   # [kind, tool, time, line, body]: kind set, user, assistant or entry
    for n, x in enumerate(text.split("\n"), 1):
        head = LSL_ENTRY.match(x) if x.startswith("### ") else None
        if x.startswith("## Prompt Set") and LSL_SET.match(x):
            blocks.append(["set", None, None, n, []])
        elif head:
            when = _iso(f"{head[3]}T{head[4]}Z") if head[3] else None
            blocks.append([(head[1] or "entry").lower(), head[2], when, n, []])
        elif blocks:
            blocks[-1][4].append(x)
    rows: list[dict] = []
    said_in_set: list[str] = []   # the prompt texts given rows in this prompt set
    set_time = None
    waiting: deque = deque()   # (tool name, command) of the calls whose results are still to come

    def shell_category(name, command):   # a result's: its call's (finish keeps it: these results have no id)
        return bash_category(command) if name and bash_tool(name) and command and command.strip() else None

    def prompt(base, words):
        if words and " ".join(words.split()) not in said_in_set:
            said_in_set.append(" ".join(words.split()))
            rows.append(_prompt_row(base, words))

    for kind, tool, when, line, body in blocks:
        while body and LSL_FILLER.match(body[-1]):
            body.pop()
        base = {"raw_file": name, "raw_line": line, "timestamp": when or set_time}
        if kind == "set":
            got = next((x for x in body if x.startswith("**Time:**")), None)
            set_time = _iso(got[len("**Time:**"):].strip()) if got else None
            said_in_set = []
            waiting.clear()
        elif kind == "user" and (result := _lsl_result(body)) is not None:
            name_, command = waiting.popleft() if waiting else (None, None)
            rows.append(_out({**base, "timestamp": set_time}, None, result[1], is_error=result[0], name=name_,
                             bash_category=shell_category(name_, command)))
        elif kind == "user":
            waiting.clear()
            prompt({**base, "timestamp": set_time}, "\n".join(body).strip())
        elif tool == "Text Exchange":
            user, said = _lsl_exchange(body)
            prompt(base, user)
            if said:
                rows.append(_say(base, said))
        elif kind == "assistant" and not next((x for x in body if x.strip()), "").startswith("**Tool:**"):
            if "\n".join(body).strip():
                rows.append(_say(base, "\n".join(body).strip()))
        else:   # a tool call: an entry of its own, or an assistant's
            call = _lsl_call(body)
            prompt(base, call["request"])
            name_ = call["tool"] or tool
            try:
                args = json.loads(call["input"]) if call["input"] else None
            except ValueError:
                args = call["input"]
            command = None
            if name_ or args is not None:
                rows.append(_call(base, name_, args, None))
                command = rows[-1]["command"]
            if call["output"] is not None or call["failed"] is not None:
                rows.append(_out(base, None, call["output"] or "", is_error=call["failed"], name=name_,
                                 bash_category=shell_category(name_, command)))
            elif name_ or args is not None:
                waiting.append((name_, command))
    return rows


# -- Aider (markdown) -----------------------------------------------------------------------------------------------
# One file holds every session started in its folder, each opening `# aider chat started at <local time>` (a row of
# its own). `#### ` lines are the user's input, `> ` lines Aider's own notices (model, warnings, a command's output),
# and the rest the model's replies, whose SEARCH/REPLACE edits stay in its text (Aider makes no tool calls). A reply
# can hold a `#### ` heading too, written as input is. Measured over the collection (2026-10-05): of 159 one-line
# `#### ` blocks between two of the model's blocks, 56 are shaped like a reply's own structure (bold, a numbered item,
# a backticked path, a line ending in a colon). Those are the model's and stay in its reply; the others read as a
# developer's short messages ("yes", questions) and stay input. The markers are taken off the text.
REPLY_SHAPE = re.compile(r"\*\*|^\d+[.)]\s|`[^`\s]*[/\\][^`\s]*`|`[^`\s]+\.[A-Za-z0-9]{1,5}`|:$")


def _reply_heading(text: str) -> bool:
    """A one-line `#### ` block shaped like a reply's structure: bold, a numbered item, a backticked path, or a line
    ending in a colon."""
    t = text.strip()
    return bool(t) and "\n" not in t and bool(REPLY_SHAPE.search(t))


# Aider's benchmark (its benchmark/benchmark.py) runs Aider on coding exercises from a script: each exercise's text,
# and each failed test run's output, is sent as input (`#### ` lines) ending in the script's fixed instructions, and each
# test run's output is written into the history as a bare fenced block, unmarked. Those prompts are the script's, not
# typed; such a block, after Aider's notices and before the next test-failure prompt or the session's end, its notice
# (each test-failure prompt opens with its block's first line). Measured over the collection on 2026-10-05: every prompt
# of paraemsi/Reducing-violations-paper-data (5,356 of Aider's 5,817 sessions) is one, and 26 elsewhere.
AIDER_BENCH = {"task": ("Use the above instructions to modify the supplied files",
                        "Don't change the names of existing functions or classes"),
               "test": ("See the testing errors above.", "The tests are correct, don't try and change them.")}
# Aider writes a notice of several lines with `> ` on its first line only, and its line-break mark (two trailing
# spaces) on its last. Its SEARCH/REPLACE failure, the commonest (1,340 in the benchmark's runs), runs from
# `# N SEARCH/REPLACE block(s) failed to match!` to one of these, marked; read whole as the notice it is, not as the
# agent's reply. Elsewhere a notice's lines are each marked `> ` (older versions), so no other notice is continued.
AIDER_FAILED = re.compile(r"# \d+ SEARCH/REPLACE blocks? failed to match!$")
AIDER_FAILED_ENDS = ("The SEARCH section must exactly match an existing block of lines including all white space, "
                     "comments, indentation, docstrings, etc",
                     "Just reply with fixed versions of the block above that failed to match.",
                     "Just reply with fixed versions of the blocks above that failed to match.")


def aider_benchmark(text: str) -> str | None:
    """Which of Aider's benchmark prompts a message is, by the fixed instructions its script appends (`task`: an
    exercise; `test`: a failed test run's output), or None."""
    return next((k for k, sentences in AIDER_BENCH.items() if all(x in text for x in sentences)), None)


def aider(files, session):
    name, local = files[0]
    blocks: list[list] = []   # [state, first line, lines]: start, user, notice or agent
    block: list[str] = []
    state, first = None, 0

    def flush():
        if state in ("user", "agent", "notice") and "\n".join(block).strip():
            blocks.append([state, first, list(block)])
        block.clear()

    for n, line in enumerate(local.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if line.startswith("# aider chat started at"):
            flush()
            state = None
            blocks.append(["start", n, [line[2:]]])
            continue
        # Aider's notices are `> ` lines (or a bare `>`); an edit's `>>>>>>> REPLACE` is the model's.
        kind = "user" if line.startswith("#### ") or line == "####" else "notice" if line.startswith("> ") or line == ">" else (
            "agent" if line.strip() else state)
        if kind != state:
            flush()
            state, first = kind, n
        if kind == "user":
            block.append(line[5:])
        elif kind == "notice":
            block.append(line[2:] if line.startswith("> ") else line[1:])
        elif kind == "agent":
            block.append(line)
    flush()
    for b, nxt in zip(blocks, blocks[1:]):   # a SEARCH/REPLACE failure's lines after its first, from the reply
        last = next((x for x in reversed(b[2]) if x.strip()), "")
        if b[0] == "notice" and nxt[0] == "agent" and AIDER_FAILED.match(last.strip()):
            end = next((j for j, x in enumerate(nxt[2]) if x.endswith("  ") and x.strip() in AIDER_FAILED_ENDS), None)
            if end is not None:
                b[2] += nxt[2][:end + 1]
                rest = end + 1
                while rest < len(nxt[2]) and not nxt[2][rest].strip():
                    rest += 1
                nxt[1], nxt[2] = nxt[1] + rest, nxt[2][rest:]
    merged: list[list] = []
    for kind, line, lines in blocks:
        text = "\n".join(lines).strip("\n")
        if not text.strip():   # a reply that was all a notice's lines
            continue
        b = [kind, line, text]
        if (b[0] == "agent" and len(merged) >= 2 and merged[-1][0] == "user" and merged[-2][0] == "agent"
                and _reply_heading(merged[-1][2])):   # the model's own heading, inside its reply
            heading = merged.pop()
            merged[-1][2] += f"\n\n#### {heading[2].strip()}\n\n{b[2]}"
            continue
        merged.append(b)
    rows: list[dict] = []
    bench = False   # this session holds the benchmark's prompts
    for i, (kind, line, text) in enumerate(merged):
        base = {"raw_file": name, "raw_line": line}
        after = merged[i + 1] if i + 1 < len(merged) else None
        if kind == "start":
            bench = False
        if kind == "user" and aider_benchmark(text):
            bench = True
            rows.append(_user(base, text, False, "harness", "notice"))
        elif kind == "user":
            rows.append(_prompt_row(base, text))
        elif (kind == "agent" and bench and merged[i - 1][0] == "notice" and text.split("\n", 1)[0].strip() == "```"
              and (after is None or after[0] == "start" or (after[0] == "user" and aider_benchmark(after[2]) == "test"))):
            rows.append(_meta(base, "system_event", "notice", text))   # the benchmark's test output
        elif kind == "agent":
            rows.append(_say(base, text))
        else:
            rows.append(_meta(base, "system_event", "notice", text))
    return finish(rows, session), []


# -- DataClaw (one record of its `conversations.jsonl` export) -------------------------------------------------------
# A record's messages: a user's text; an agent's thinking, text and tool calls, each call carrying its output. The
# export keeps user messages as text with nothing to mark what the tool inserted: the developer's, unless the text
# shows a harness's (formats._prompt_row).
_OFFSETS: dict[str, list[int]] = {}


def _record(name: str, local: Path) -> tuple[str, int, dict]:
    """A record of an export by its line, read where the line starts: each file's lines are indexed once a process,
    not read from the top for every record (a 1 GB export of 5,000 records would be read 5,000 times)."""
    rel, line = name.rsplit(":", 1)
    key = str(local)
    if key not in _OFFSETS:   # kept for every file: a worker's records come from several files, interleaved
        starts, pos = [], 0
        with open(local, "rb") as fh:
            for raw in fh:
                starts.append(pos)
                pos += len(raw)
        _OFFSETS[key] = starts
    n = int(line)
    if not 1 <= n <= len(_OFFSETS[key]):
        raise ValueError(f"no line {line} in {rel}")
    with open(local, "rb") as fh:
        fh.seek(_OFFSETS[key][n - 1])
        return rel, n, json.loads(fh.readline())


def dataclaw(files, session):
    rel, line, rec = _record(*files[0])
    rows: list[dict] = []
    for i, m in enumerate(rec.get("messages") or []):
        if not isinstance(m, dict):
            continue
        base = {"raw_file": rel, "raw_line": line, "timestamp": _iso(m.get("timestamp")), "content_block_index": i,
                "model": rec.get("model") if isinstance(rec.get("model"), str) else None}
        content = m.get("content")
        text = content if isinstance(content, str) else _text(content)
        if m.get("role") == "user":
            if text.strip():
                rows.append(_prompt_row({**base, "model": None}, text))
            continue
        since = len(rows)
        if isinstance(m.get("thinking"), str) and m["thinking"].strip():
            rows.append(_think(base, m["thinking"]))
        if text.strip():
            rows.append(_say(base, text))
        for n, t in enumerate(m.get("tool_uses") or []):
            if not isinstance(t, dict):
                continue
            cid = t.get("id") or t.get("tool_use_id") or f"{line}.{i}.{n}"
            rows.append(_call(base, t.get("tool") or t.get("name"), t.get("input"), cid))
            out = t.get("output")
            if out is not None:
                rows.append(_out({**base, "model": None}, cid, out.get("text") if isinstance(out, dict) else out,
                                 is_error=str(t.get("status") or "").lower() in ("error", "failed")))
        u = m.get("usage") if isinstance(m.get("usage"), dict) else {}
        _place(rows, _ints(input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"),
                           cache_read_input_tokens=u.get("cache_read_input_tokens"),
                           cache_creation_input_tokens=u.get("cache_creation_input_tokens")), since)
    return finish(rows, session), []


# -- OpenHands (one trajectory of the feedback dataset) ---------------------------------------------------------------
# Events by source (user, agent, environment): a user's message is typed in OpenHands' interface; an agent's message
# is its text; any other action of the agent is a tool call (its `thought`, the text it gave with it, before it), and
# an observation its result; state changes are events.
_TABLES: dict[str, list] = {}


def openhands(files, session):
    name, local = files[0]
    rel, idx = name.rsplit(":", 1)
    if str(local) not in _TABLES:   # read once a process: every session of the file is one of its rows
        import pyarrow.parquet as pq

        _TABLES.clear()
        _TABLES[str(local)] = pq.read_table(local, columns=["trajectory"]).column("trajectory").to_pylist()
    events = _TABLES[str(local)][int(idx)] or []
    rows: list[dict] = []
    for e in events:
        src, action, obs = e.get("source"), e.get("action"), e.get("observation")
        base = {"raw_file": rel, "raw_line": int(idx), "source_entry_uuid": str(e["id"]) if e.get("id") is not None else None,
                "timestamp": _iso(e["timestamp"].isoformat() + "Z") if e.get("timestamp") is not None else None}
        try:
            extras = json.loads(e["extras"]) if e.get("extras") else {}
        except ValueError:
            extras = {"extras": e.get("extras")}
        extras = extras if isinstance(extras, dict) else {"extras": extras}
        text = e.get("content") or e.get("message") or ""
        if action == "message":
            if src == "user":
                rows.append(_user(base, text, True, "developer"))
            elif text.strip():
                rows.append(_say(base, text))
        elif src == "user" and action == "run":   # the developer ran a command in the terminal
            rows.append(_user(base, extras.get("command") or text, False, "developer", "shell", command=extras.get("command")))
        elif action and src == "agent":
            if str(extras.get("thought") or "").strip():
                rows.append(_say(base, extras["thought"]))
            args = {k: v for k, v in extras.items() if k != "thought"}
            rows.append(_call(base, action, args, str(e.get("id"))))
        elif action:
            rows.append(_meta(base, "system_event", "notice", text or None))
        elif obs and obs not in ("agent_state_changed", "null"):
            cause = extras.get("cause") if isinstance(extras.get("cause"), (int, str)) else None
            rows.append({**_out(base, str(cause) if cause is not None else None, e.get("content") or "",
                                is_error=obs == "error", name=obs), "actor": "tool" if src != "user" else "harness",
                         "kind": "tool_result" if src != "user" else "shell"})
        elif obs:
            rows.append(_meta(base, "system_event", "notice"))
    return finish(rows, session), []


BY_SOURCE = {"specstory": specstory, "aider": aider}
READERS = {"dataclaw": dataclaw, "openhands": openhands}

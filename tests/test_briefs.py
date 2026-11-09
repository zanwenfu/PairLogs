"""collect.briefs: a helper agent's brief, filed as its own session's first "user" row, found and marked as another
agent's text; a person's own words handed on to a helper, and every later row, left typed."""
import json

import pyarrow as pa
import pyarrow.parquet as pq

from collect import briefs

BRIEF = "Explore the ImageGallery component and its lightbox. I need to understand: 1. how navigation works"
ECHO = "Please refactor the payment module so that retries use exponential backoff and log each failure"


def _conv(rows):
    cols = ("session_key", "turn_number", "role", "typed_by_developer", "actor", "kind", "category",
            "tool_input_json", "content", "source", "pushback", "copy_of")
    return pa.table({c: [r.get(c) for r in rows] for c in cols},
                    schema=pa.schema([("session_key", pa.string()), ("turn_number", pa.int64()),
                                      ("role", pa.string()), ("typed_by_developer", pa.bool_()),
                                      ("actor", pa.string()), ("kind", pa.string()), ("category", pa.string()),
                                      ("tool_input_json", pa.string()), ("content", pa.string()),
                                      ("source", pa.string()), ("pushback", pa.bool_()),
                                      ("copy_of", pa.int64())]))


def typed(key, turn, text, pushback=False):
    return {"session_key": key, "turn_number": turn, "role": "user", "typed_by_developer": True,
            "actor": "developer", "kind": "text", "content": text, "source": "entire", "pushback": pushback}


def call(key, turn, prompt):
    return {"session_key": key, "turn_number": turn, "role": "assistant", "typed_by_developer": False,
            "actor": "agent", "kind": "tool_call", "category": "Orchestration", "source": "entire",
            "tool_input_json": json.dumps({"description": "explore", "prompt": prompt})}


def version(tmp_path):
    v = tmp_path / "2026-11-02"
    for sub in ("conversations/entire", "conversations/dataclaw", "data/entire", "data/dataclaw"):
        (v / sub).mkdir(parents=True)
    rows = [typed("P", 0, "how does the gallery handle swipes between pages, can you find out"), call("P", 1, BRIEF),
            typed("C", 0, BRIEF, pushback=True), typed("C", 3, "and also check the thumbnails please, all of them"),
            typed("E", 0, ECHO), call("E", 1, ECHO),          # a person's words handed on by its own agent
            typed("E2", 0, ECHO),                              # a copy of E: the same session, not a helper
            typed("O", 0, "fix it"), typed("O", 4, "now the tests too"),   # an OpenCode helper session
            typed("S", 2, BRIEF), typed("S", 0, "a long enough first message  that is\nthe person's own words"),
            {**typed("S", 5, BRIEF), "copy_of": 2},            # S's queued brief delivered: a copy, not counted
            typed("T", 0, "short"), call("T", 1, "short"),     # too short to match
            typed("X", 0, "explain the build script to me please"),   # Haiku only, but not DataClaw's
            {**typed("F", 0, "how does the gallery handle swipes between pages, can you find out"), "copy_of": -1},
            typed("F", 1, BRIEF)]   # a forked helper thread (Codex): the parent's message inherited, then the brief
    pq.write_table(_conv(rows), v / "conversations" / "entire" / "part-000.parquet")
    dc = lambda key, turn, text, **kw: {**typed(key, turn, text), "source": "dataclaw", **kw}
    pq.write_table(_conv([dc("H", 0, "Explore the payment module. I need to understand: 1. retries"),
                          dc("H", 2, "Now also list every caller of the retry helper"),
                          dc("H", 4, "Now also list every caller of the retry helper", copy_of=2),
                          dc("M", 0, "please fix the flaky login test"),        # Haiku among other models
                          dc("N", 0, "what does this function return")]),       # no agent named
                   v / "conversations" / "dataclaw" / "part-000.parquet")
    haiku, sonnet = "claude-haiku-4-5-20251001", "claude-sonnet-4-5-20250929"
    sessions = pa.table({
        "session_key": ["P", "C", "E", "E2", "O", "S", "T", "X", "F"],
        "duplicate_of": [None, None, None, "E", None, None, None, None, None],
        "agent": ["Claude Code", "Claude Code", "Claude Code", "Claude Code", "OpenCode", "Claude Code", "Codex",
                  "Claude Code", "Codex"],
        "models": [[sonnet], [haiku], [sonnet], [sonnet], ["big-pickle"], [sonnet], [], [haiku], ["gpt-5"]],
        "files": [[], [], [], [], [{"path": "s.json", "content": '{"id":"ses_b","parentID": "ses_a"}'}], [], [], [],
                  []],
        "n_user_messages": [1, 2, 1, 1, 2, 2, 1, 1, 1],
        "source_facts": [None, json.dumps({"typed_fingerprint": "old", "x": 1}), None, None,
                         json.dumps({"typed_fingerprint": "old"}), json.dumps({"typed_fingerprint": "s"}), None, None,
                         None]})
    pq.write_table(sessions, v / "data" / "entire" / "part-000.parquet")
    pq.write_table(pa.table({
        "session_key": ["H", "M", "N"], "duplicate_of": [None, None, None], "agent": ["Claude Code", "Claude Code", None],
        "models": [[haiku], [sonnet, haiku], [haiku]], "files": [[], [], []], "n_user_messages": [2, 1, 1],
        "source_facts": [json.dumps({"typed_fingerprint": "old"}), None, None]}),
        v / "data" / "dataclaw" / "part-000.parquet")
    return v


HELPER_ROWS = [("C", 0), ("C", 3), ("F", 1), ("H", 0), ("H", 2), ("H", 4), ("O", 0), ("O", 4)]


def test_find_marks_every_typed_row_of_helper_sessions(tmp_path):
    got = briefs.find(version(tmp_path))
    assert [(k, t) for k, t, _ in got["rows"]] == HELPER_ROWS
    assert dict((k, r) for k, _, r in got["rows"]) == {"C": "matches another session's brief",
                                                         "F": "matches another session's brief",
                                                         "H": "dataclaw haiku-only session",
                                                         "O": "opencode helper session"}
    assert got["counts"] == {"matches another session's brief / entire": 2, "dataclaw haiku-only session / dataclaw": 1,
                             "opencode helper session / entire": 1}
    assert got["row_counts"] == {"matches another session's brief / entire": 3,
                                 "dataclaw haiku-only session / dataclaw": 3, "opencode helper session / entire": 2}


def test_a_persons_sessions_stay_typed(tmp_path):
    keys = {k for k, _, _ in briefs.find(version(tmp_path))["rows"]}
    assert "E" not in keys and "E2" not in keys      # the brief came from the session itself, or its kept copy
    assert "S" not in keys                           # the brief is not the session's first typed row
    assert "T" not in keys                           # under 40 characters
    assert "P" not in keys                           # the parent: it sent the brief
    assert not keys & {"M", "N", "X"}                # Haiku with others; no agent named; Haiku, but not DataClaw's


def test_apply_rewrites_rows_and_counts(tmp_path):
    v = version(tmp_path)
    rows = briefs.find(v)["rows"]
    counts = briefs.apply(v, rows)
    assert counts["rows marked another agent's"] == 8
    t = pq.read_table(v / "conversations" / "entire" / "part-000.parquet").to_pylist()
    t += pq.read_table(v / "conversations" / "dataclaw" / "part-000.parquet").to_pylist()
    at = {(r["session_key"], r["turn_number"]): r for r in t}
    assert (at[("C", 0)]["typed_by_developer"], at[("C", 0)]["actor"], at[("C", 0)]["pushback"]) == (False, "peer", None)
    assert {k for k, r in at.items() if r["actor"] == "peer"} == set(HELPER_ROWS)
    assert all(at[k]["typed_by_developer"] for k in (("E", 0), ("P", 0), ("S", 0), ("M", 0), ("N", 0), ("X", 0)))
    assert at[("F", 0)]["typed_by_developer"] and at[("F", 0)]["actor"] == "developer"   # inherited: the person's
    s = {}
    for sub in ("entire", "dataclaw"):
        s.update(zip(*pq.read_table(v / "data" / sub / "part-000.parquet",
                                    columns=["session_key", "n_user_messages"]).to_pydict().values()))
    assert s == {"P": 1, "C": 0, "E": 1, "E2": 1, "O": 0, "S": 2, "T": 1, "X": 1, "F": 0, "H": 0, "M": 1, "N": 1}
    assert briefs.apply(v, rows)["rows marked another agent's"] == 0    # a second run changes nothing


def test_apply_to_label_tables_clears_text_id(tmp_path):
    b = tmp_path / "b"
    (b / "labels" / "conversations" / "entire").mkdir(parents=True)
    pq.write_table(pa.table({"session_key": ["C", "C", "C"], "turn_number": [0, 3, 5],
                             "typed_by_developer": [True, True, True], "actor": ["developer"] * 3,
                             "copy_of": pa.array([None, None, 0], pa.int64()), "pushback": [True, False, True],
                             "text_id": ["aaa", "bbb", "ccc"]}),
                   b / "labels" / "conversations" / "entire" / "part-000.parquet")
    pq.write_table(pa.table({"session_key": ["C"], "n_user_messages": [2]}), b / "pointers.parquet")
    briefs.apply(b, [("C", 0, "matches another session's brief")])
    t = pq.read_table(b / "labels" / "conversations" / "entire" / "part-000.parquet").to_pylist()
    assert t[0] == {"session_key": "C", "turn_number": 0, "typed_by_developer": False, "actor": "peer",
                    "copy_of": None, "pushback": None, "text_id": None}
    assert t[1]["text_id"] == "bbb" and t[1]["typed_by_developer"] is True
    assert (t[2]["typed_by_developer"], t[2]["actor"], t[2]["text_id"]) == (False, "peer", None)   # its copy
    assert pq.read_table(b / "pointers.parquet")["n_user_messages"].to_pylist() == [1]       # the copy not counted


def test_brief_key():
    assert briefs.brief_key("  Explore   THE\ncode " + "x" * 50) == "explore the code " + "x" * 50
    assert briefs.brief_key("short") is None
    assert len(briefs.brief_key("y" * 1000)) == 300


def test_cli_marks_once(tmp_path, capsys):
    from collect import cli
    v = version(tmp_path)
    (v / "changelog.json").write_text(json.dumps({"version": "2026-11-02"}))
    assert cli.main(["briefs", "--version-dir", str(v)]) == 0                 # a dry run writes nothing
    assert "helper_briefs" not in json.loads((v / "changelog.json").read_text())
    assert cli.main(["briefs", "--version-dir", str(v), "--apply"]) == 0
    log = json.loads((v / "changelog.json").read_text())["helper_briefs"]
    assert (log["rows"], log["sessions"]) == (8, 4)
    assert log["typed_fingerprints"] == {"fingerprints changed": 0, "fingerprints removed": 3}
    facts = dict(zip(*pq.read_table(v / "data" / "entire" / "part-000.parquet",
                                    columns=["session_key", "source_facts"]).to_pydict().values()))
    assert (facts["C"], facts["O"]) == ('{"x": 1}', "{}")      # as the build writes it
    assert facts["F"] is None and facts["P"] is None             # none stored, none written
    t = pq.read_table(v / "conversations" / "entire" / "part-000.parquet").to_pylist()
    t += pq.read_table(v / "conversations" / "dataclaw" / "part-000.parquet").to_pylist()
    assert {(r["session_key"], r["turn_number"]) for r in t if r["actor"] == "peer"} == set(HELPER_ROWS)
    assert cli.main(["briefs", "--version-dir", str(v), "--apply"]) == 1      # one pass per folder, its record kept
    assert json.loads((v / "changelog.json").read_text())["helper_briefs"] == log


def test_fingerprints_from_the_typed_rows_left(tmp_path):
    import hashlib
    v = version(tmp_path)
    rows = briefs.find(v)["rows"]
    briefs.apply(v, rows)
    fps = briefs.fingerprints(v, {"C", "O", "S", "H"})
    want = hashlib.sha256("\x1f".join(["a long enough first message that is the person's own words",
                                        " ".join(BRIEF.split())]).encode()).hexdigest()
    assert fps == {"C": None, "O": None, "S": want, "H": None}   # helpers keep no typed row; S, untouched, two
    got = briefs.set_fingerprints(v, {"C": None, "O": None})
    assert got["fingerprints removed"] == 2
    facts = dict(zip(*pq.read_table(v / "data" / "entire" / "part-000.parquet",
                                    columns=["session_key", "source_facts"]).to_pydict().values()))
    assert json.loads(facts["C"]) == {"x": 1} and json.loads(facts["O"]) == {}
    assert json.loads(facts["S"]) == {"typed_fingerprint": "s"} and facts["P"] is None
    assert briefs.set_fingerprints(v, {"C": None, "O": None}) == {}   # a second run changes nothing

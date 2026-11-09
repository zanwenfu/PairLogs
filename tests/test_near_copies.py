"""Near-copies: one conversation stored as two kept sessions, made a duplicate of the fuller one (dedup.near_copies,
build.near_copy_pass); separate runs of the same prompts, and one short message alike, kept apart."""
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from collect import build, conv_build, dedup  # noqa: E402

LONG = ["Refactor the session parser so each format has its own reader and tests",
        "Now move the masking out of the reader and into the build's staging step please",
        "Add a regression test for the escaped home path we saw in the Windows transcripts"]


def row(sid, repo="o/r", source="entire", **kw):
    return {"source": source, "source_id": sid, "repo": repo, "dedup_key": f"sha256:{sid}", "has_tool_results": True,
            "bytes": 100, "found_url": None, "duplicate_of": None, "also_in": [], **kw}


def summ(typed, texts, tools=(), sid=None):
    d = conv_build._digest
    return {"typed": [d(t) for t in typed], "chars": sum(len(t) for t in typed), "texts": sorted({d(a) for a in texts}),
            "tools": sorted({d(a) for a in tools}), "whole": d("|".join([*typed, *texts, *tools])), "sid": sid}


def run(rows, sums):
    return dedup.near_copies(rows, {f"{r['source']}:{r['source_id']}": s for r, s in zip(rows, sums)}, build.scope_of)


def dup(r):
    return r["duplicate_of"]


def test_the_same_conversation_stored_twice_keeps_the_fuller():
    a, b = row("o/r:a"), row("o/r:b")
    pairs = run([a, b], [summ(["fix the parser", "and the tests"], ["r1", "r2", "r3"]),
                         summ(["fix the parser", "and the tests"], ["r1", "r2", "r3", "r4"])])
    assert (dup(a), dup(b)) == ("entire:o/r:b", None) and pairs == Counter({"entire~entire": 1})
    assert [x["source_id"] for x in a["also_in"]] == ["o/r:b"] and [x["source_id"] for x in b["also_in"]] == ["o/r:a"]


def test_an_earlier_save_of_a_longer_session_is_its_copy():
    early, late = row("o/r:early"), row("o/r:late")
    run([early, late], [summ(["fix the parser", "and the tests"], ["r1", "r2", "r3"]),
                        summ(["fix the parser", "and the tests", "now push"], ["r1", "r2", "r3", "r4", "r5"])])
    assert (dup(early), dup(late)) == ("entire:o/r:late", None)


def test_separate_runs_of_the_same_prompts_stay_apart_even_reading_the_same_files():
    a, b = row("o/r:a"), row("o/r:b")
    pairs = run([a, b], [summ(["fix the parser", "and the tests"], ["r1", "r2", "r3"], ["read a", "read b", "read c"]),
                         summ(["fix the parser", "and the tests"], ["s1", "s2", "s3"], ["read a", "read b", "read c"])])
    assert (dup(a), dup(b), pairs) == (None, None, Counter())
    # two runs of /review: the same files read, other findings (35 merged before tool calls stopped counting)
    c, d = row("o/r:c"), row("o/r:d")
    run([c, d], [summ(["/review"], ["Found one correctness issue"], ["read x", "read y", "read z", "grep w"]),
                 summ(["/review"], ["I found one high-impact issue"], ["read x", "read y", "read z", "grep w"])])
    assert (dup(c), dup(d)) == (None, None)


def test_a_long_typed_side_alike_is_one_conversation_where_the_agents_sides_do_not_differ():
    # DataClaw's normalized copy of a session mostly of tool calls: too few replies to tell, the calls alike
    raw, normalized = row("o/r:raw"), row("o/r:normalized")
    run([raw, normalized], [summ(LONG, ["r1"], ["t1", "t2", "t3", "t4"]), summ(LONG, ["n1"], ["t1", "t2", "t3"])])
    assert (dup(raw), dup(normalized)) == (None, "entire:o/r:raw")
    # an export that rendered the replies anew: the typed side alike, a tenth of the replies alike
    early, late = row("o/r:early"), row("o/r:late")
    run([early, late], [summ(LONG, ["a", "b"] + [f"e{i}" for i in range(18)]),
                        summ(LONG, ["a", "b"] + [f"l{i}" for i in range(30)])])
    assert (dup(early), dup(late)) == ("entire:o/r:late", None)
    # runs of the same prompts: plenty of replies, almost none alike (1 of 54 seen in the data; here 2 of 32, under a tenth)
    p, q = row("o/r:p"), row("o/r:q")
    run([p, q], [summ(LONG, ["a", "b"] + [f"p{i}" for i in range(30)]),
                 summ(LONG, ["a", "b"] + [f"q{i}" for i in range(50)])])
    assert (dup(p), dup(q)) == (None, None)
    a, b = row("o/r:a"), row("o/r:b")   # too few replies to tell, nothing of the agents' alike: separate runs
    run([a, b], [summ(LONG, ["r1", "r2"], ["t1", "t2"]), summ(LONG, ["s1", "s2"], ["t1", "t2"])])
    assert (dup(a), dup(b)) == (None, None)
    short_a, short_b = row("o/r:sa"), row("o/r:sb")   # two prompts alike: not enough alone
    run([short_a, short_b], [summ(LONG[:2], ["r1"], ["t1", "t2", "t3"]), summ(LONG[:2], ["r1"], ["t1", "t2", "t3"])])
    assert (dup(short_a), dup(short_b)) == (None, None)


def test_a_short_message_and_a_canned_reply_alike_are_not_a_copy():
    a, b = row("o/r:a"), row("o/r:b")
    run([a, b], [summ(["hi"], ["Hello! How can I help?"]), summ(["hi"], ["Hello! How can I help?"])])
    assert (dup(a), dup(b)) == (None, None)


def test_only_within_one_repository_or_export():
    a, b = row("o/r:a"), row("p/q:b", repo="p/q")
    run([a, b], [summ(LONG, ["r1", "r2", "r3"]), summ(LONG, ["r1", "r2", "r3"])])
    assert (dup(a), dup(b)) == (None, None)
    c, d = row("u/one:conversations.jsonl:1", repo=None, source="dataclaw"), row("u/one:per-day.jsonl:4", repo=None,
                                                                                   source="dataclaw")
    e = row("u/two:conversations.jsonl:1", repo=None, source="dataclaw")   # another export: another scope
    run([c, d, e], [summ(LONG, ["r1", "r2", "r3", "r4"]), summ(LONG, ["r1", "r2", "r3"]), summ(LONG, ["r1", "r2", "r3"])])
    assert (dup(c), dup(d), dup(e)) == (None, "dataclaw:u/one:conversations.jsonl:1", None)


def test_saves_in_a_chain_point_to_the_fullest_and_a_copys_own_copies_follow():
    s1, s2, s3 = row("o/r:s1"), row("o/r:s2"), row("o/r:s3")
    exact = row("o/r:exact", source="swe-chat-v2", duplicate_of="entire:o/r:s1")   # assign's copy of s1
    run([s1, s2, s3, exact], [summ(["a1", "a2"], ["r1", "r2", "r3"]), summ(["a1", "a2", "a3"], ["r1", "r2", "r3", "r4"]),
                              summ(["a1", "a2", "a3", "a4"], ["r1", "r2", "r3", "r4", "r5"]), None])
    assert (dup(s1), dup(s2), dup(s3), dup(exact)) == ("entire:o/r:s3", "entire:o/r:s3", None, "entire:o/r:s3")
    assert sorted(x["source_id"] for x in s3["also_in"]) == ["o/r:exact", "o/r:s1", "o/r:s2"]


def test_the_choice_is_the_same_whatever_the_order():
    def result(seed):
        rows = [row(f"o/r:{i}") for i in range(6)]
        sums = [summ(["p1", "p2"] + ["p3"] * (i % 3), ["r1", "r2", "r3"] + [f"x{i}"] * (i % 2)) for i in range(6)]
        both = list(zip(rows, sums))
        random.Random(seed).shuffle(both)
        run([r for r, _ in both], [s for _, s in both])
        return sorted((r["source_id"], r["duplicate_of"]) for r in rows)
    assert result(1) == result(2) == result(3)


def test_the_limits_one_at_a_time():
    a, b = row("o/r:a"), row("o/r:b")   # 3 of 10 replies in common: under half
    run([a, b], [summ(["fix it", "push"], [f"r{i}" for i in range(10)]),
                 summ(["fix it", "push"], ["r0", "r1", "r2"] + [f"s{i}" for i in range(7)])])
    assert (dup(a), dup(b)) == (None, None)
    two = ["x" * 150, "y" * 150]   # two long messages alike: not three
    c, d = row("o/r:c"), row("o/r:d")
    run([c, d], [summ(two, ["r1"], ["t1", "t2", "t3"]), summ(two, ["r1"], ["t1", "t2", "t3"])])
    assert (dup(c), dup(d)) == (None, None)
    three = ["ok", "go", "push"]   # three short messages alike: not 200 characters
    e, f = row("o/r:e"), row("o/r:f")
    run([e, f], [summ(three, ["r1"], ["t1", "t2", "t3"]), summ(three, ["r1"], ["t1", "t2", "t3"])])
    assert (dup(e), dup(f)) == (None, None)
    g, h = row("o/r:g"), row("o/r:h")   # a long typed side and two tool calls in common: not enough
    run([g, h], [summ(LONG, ["r1"], ["t1", "t2"]), summ(LONG, ["s1"], ["t1", "t2"])])
    assert (dup(g), dup(h)) == (None, None)


def test_a_command_counts_by_its_name_not_its_markup():
    rows = [{"typed_by_developer": True, "copy_of": None, "kind": "command", "command": "/clear", "actor": "developer",
             "turn_type": "user_prompt", "content": "<command-name>/clear</command-name> <command-message>clear"
             "</command-message> <command-args></command-args>", "tool_name": None, "tool_input_json": None}]
    s = conv_build.summarize(rows)
    assert s["typed"] == [conv_build._digest("/clear")] and s["chars"] == 6


def test_a_dataclaw_records_summary_reads_as_its_rows_do(tmp_path, monkeypatch):
    from test_copied_sessions import files_of, write_jsonl
    from collect import copied_sessions
    loop = "<command-message>loop</command-message>\n<command-name>/loop</command-name>\n<command-args>check</command-args>"
    root = tmp_path / "u__data" / "rev"
    write_jsonl(root / "conversations.jsonl", [
        {"session_id": "s-1", "source": "claude", "model": "m",
         "messages": [{"role": "user", "content": loop}, {"role": "assistant", "content": "ok"},
                      {"role": "user", "content": loop}, {"role": "assistant", "content": "checked"},
                      {"role": "user", "content": "@agent Try Again"}, {"role": "assistant", "content": "again"},
                      {"role": "user", "content": "fix the flaky test"}, {"role": "assistant", "content": "fixed"}]}])
    entry = {"id": "u/data", "url": "https://huggingface.co/datasets/u/data", "revision": "rev", "license": "mit",
             "copied": True, "files": files_of(root)}
    monkeypatch.setattr(copied_sessions, "entries", lambda ledger: iter([("dataclaw", entry)]))
    rows = copied_sessions.rows(None)["dataclaw"]
    got = conv_build.summary_job(("dataclaw", [(r, build._raw_files(r)) for r in rows]))
    s = got["dataclaw:u/data:conversations.jsonl:1"]
    assert len(s["typed"]) == 2 and len(s["texts"]) == 4 and s["tools"] == []   # the first /loop and the request
    assert s["typed"] == [conv_build._digest("/loop check"), conv_build._digest("fix the flaky test")]
    assert s["sid"] == "s-1" and isinstance(s["whole"], int)


def test_summaries_are_kept_where_a_sessions_content_has_not_moved(tmp_path):
    base = tmp_path / "2026-10-04"
    base.mkdir()
    a, b = row("o/r:a"), row("o/r:b")
    build.write_summaries(base / build.SUMMARIES, {
        "entire:o/r:a": {**summ(LONG, ["r1", "r2", "r3"]), "fp": build.content_fp(a)},
        "entire:o/r:b": {**summ(LONG, ["r1", "r2", "r3", "r4"]), "fp": build.content_fp(b)}})
    sums, pairs, read, same = build.near_copy_pass({"entire": [a, b]}, base, None, 2, log=lambda m: None)
    assert read == 0 and (dup(a), dup(b)) == ("entire:o/r:b", None) and set(sums) == {"entire:o/r:a", "entire:o/r:b"}
    # whose copy it is does not move its content: a copy's summary is kept too
    assert build.content_fp(a) == build.content_fp({**a, "duplicate_of": None})


def test_a_copied_row_matches_the_session_kept_through_a_near_copy(tmp_path):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    from collect import hfcopy_build
    (tmp_path / "data" / "entire").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"dedup_key": "claude-code:s1", "source": "entire", "source_id": "o/r:s1", "duplicate_of": None},
        {"dedup_key": "claude-code:s2", "source": "entire", "source_id": "o/r:s2", "duplicate_of": "entire:o/r:s1"}]),
        str(tmp_path / "data" / "entire" / "part-000.parquet"))
    export = tmp_path / "conversations.jsonl"
    export.write_text(json.dumps({"project": "claude-code:o/r", "session_id": "s2"}) + "\n")
    items = [{"name": "dataclaw", "entry": {"id": "u/data"}, "file": {"local": str(export), "path": "conversations.jsonl"}}]
    assert hfcopy_build.matches_in(items, tmp_path) == 1
    got = pq.read_table(tmp_path / "copied" / "matches.parquet").to_pylist()[0]
    assert (got["session_key"], got["matched_row"]) == ("claude-code:s2", "entire:o/r:s1")


# -- copies under one session id (dedup.same_session_copies) --------------------------------------------------------
def both(rows, sums):
    s = {f"{r['source']}:{r['source_id']}": x for r, x in zip(rows, sums)}
    near = dedup.near_copies(rows, s, build.scope_of)
    return near, dedup.same_session_copies(rows, s)


def claw(sid):
    return row(sid, repo=None, source="dataclaw")


def test_one_session_copied_into_two_exports_is_a_copy():
    # DataClaw records no agent: its key is the raw line, and two exports are two near-copy scopes
    a, b = claw("u/one:conversations.jsonl:3"), claw("u/two:conversations.jsonl:9")
    near, same = both([a, b], [summ(LONG, ["r1", "r2"], sid="s-7"), summ(LONG, ["r1", "r2"], sid="s-7")])
    assert near == Counter() and same == Counter({"dataclaw~dataclaw": 1})
    assert (dup(a), dup(b)) == (None, "dataclaw:u/one:conversations.jsonl:3")
    assert [x["source_id"] for x in a["also_in"]] == ["u/two:conversations.jsonl:9"]
    # too short to judge by its content, in one export: the id and the whole conversation tell
    c, d = claw("u/one:conversations.jsonl:1"), claw("u/one:conversations.jsonl:2")
    near, same = both([c, d], [summ(["hi"], ["Hello!"], sid="s-1"), summ(["hi"], ["Hello!"], sid="s-1")])
    assert near == Counter() and (dup(c), dup(d)) == (None, "dataclaw:u/one:conversations.jsonl:1")


def test_the_same_id_with_another_conversation_or_the_same_conversation_under_another_id_stays_apart():
    a, b = claw("u/one:c.jsonl:1"), claw("u/two:c.jsonl:1")
    both([a, b], [summ(["hi"], ["Hello!"], sid="s-1"), summ(["hi"], ["Hello there!"], sid="s-1")])
    assert (dup(a), dup(b)) == (None, None)
    c, d = claw("u/one:c.jsonl:2"), claw("u/two:c.jsonl:2")
    both([c, d], [summ(["hi"], ["Hello!"], sid="s-1"), summ(["hi"], ["Hello!"], sid="s-2")])
    assert (dup(c), dup(d)) == (None, None)
    e, f = claw("u/one:c.jsonl:3"), claw("u/two:c.jsonl:3")   # no id recorded
    both([e, f], [summ(["hi"], ["Hello!"]), summ(["hi"], ["Hello!"])])
    assert (dup(e), dup(f)) == (None, None)


def test_a_copy_under_one_id_keeps_by_rank_and_its_own_copies_follow():
    a, b = claw("u/one:c.jsonl:1"), row("o/r:s-9", source="entire", has_tool_results=False)
    exact = claw("u/three:c.jsonl:5")
    exact.update(duplicate_of="dataclaw:u/one:c.jsonl:1")   # assign's copy of a
    near, same = both([a, b, exact], [summ(LONG, ["r1"], sid="s-9"), summ(LONG, ["r1"], sid="s-9"), None])
    assert same == Counter({"dataclaw~entire": 1})   # tool results first (_rank): the DataClaw row is kept
    assert (dup(a), dup(b), dup(exact)) == (None, "dataclaw:u/one:c.jsonl:1", "dataclaw:u/one:c.jsonl:1")
    assert sorted(x["source_id"] for x in a["also_in"]) == ["o/r:s-9", "u/three:c.jsonl:5"]


def test_the_whole_conversation_is_every_rows_role_text_and_tool_input_in_order():
    rows = [{"role": "user", "content": "fix it", "typed_by_developer": True, "copy_of": None, "kind": "text",
             "actor": "developer", "turn_type": "user_prompt", "tool_name": None, "tool_input_json": None},
            {"role": "assistant", "content": "", "typed_by_developer": False, "copy_of": None, "kind": "tool_use",
             "actor": "agent", "turn_type": "tool_use", "tool_name": "Bash", "tool_input_json": '{"command": "ls"}'}]
    w = conv_build.summarize(rows)["whole"]
    assert conv_build.summarize([dict(r) for r in rows])["whole"] == w
    assert conv_build.summarize(rows[::-1])["whole"] != w
    assert conv_build.summarize([rows[0], {**rows[1], "tool_input_json": '{"command": "pwd"}'}])["whole"] != w
    assert conv_build.summarize([{**rows[0], "role": "assistant"}, rows[1]])["whole"] != w


def test_summaries_from_before_the_whole_conversation_are_read_again(tmp_path):
    base = tmp_path / "2026-10-28"
    base.mkdir()
    a = row("o/r:a")
    old = {k: v for k, v in summ(LONG, ["r1"]).items() if k not in ("whole", "sid")}
    import pyarrow.parquet as pq
    build.write_summaries(base / build.SUMMARIES, {"entire:o/r:a": {**old, "fp": build.content_fp(a)}})
    f = base / build.SUMMARIES   # as version 2026-10-28 wrote it: no `whole`, no `sid`
    pq.write_table(pq.read_table(f).drop_columns(["whole", "sid"]), str(f))
    assert build.read_summaries(f)["entire:o/r:a"]["whole"] is None

    class Pool:
        map = staticmethod(lambda f, jobs: map(f, jobs))
    sums, pairs, read, same = build.near_copy_pass({"entire": [a]}, base, Pool(), 2, log=lambda m: None)
    assert read == 1

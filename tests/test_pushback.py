"""`pushback` by text (collect.pushback, #22): a text labelled once keeps its label wherever it appears again."""

import hashlib
import json

import pyarrow as pa
import pyarrow.parquet as pq

from collect import pushback as P

SCHEMA = pa.schema([("typed_by_developer", pa.bool_()), ("content", pa.large_string()), ("pushback", pa.bool_())])


def test_a_texts_id_is_extract_typeds():
    assert P.text_id("no, revert that") == hashlib.sha256(b"no, revert that").hexdigest()[:24]
    assert P.text_id(None) == P.text_id("") and len(P.text_id("x")) == 24


def test_typed_rows_take_their_texts_label_and_every_other_row_none(tmp_path):
    path = tmp_path / "part-000.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"typed_by_developer": True, "content": "no, revert that", "pushback": None},
        {"typed_by_developer": True, "content": "thanks", "pushback": None},
        {"typed_by_developer": True, "content": "a text the model refused", "pushback": None},
        {"typed_by_developer": True, "content": "never labelled", "pushback": None},
        {"typed_by_developer": False, "content": "no, revert that", "pushback": True},   # not typed: emptied
        {"typed_by_developer": None, "content": "who wrote this", "pushback": None},
    ], schema=SCHEMA), path)
    store = tmp_path / "labels.jsonl"
    store.write_text("\n".join(json.dumps(x) for x in [
        {"id": P.text_id("no, revert that"), "label": 0},   # replaced by the retry below
        {"id": P.text_id("thanks"), "label": 0},
        {"id": P.text_id("a text the model refused"), "label": None, "answer": "content_filter"},
        {"id": P.text_id("no, revert that"), "label": 1}]) + "\n")
    labels = P.load(store)
    dry = P.apply([path], labels, write=False)
    assert dry["counts"] == {"pushback": 1, "not pushback": 1, "typed, refused": 1, "typed, no result yet": 1,
                             "differ": 3}, dry["counts"]
    assert dry["missing"] == {P.text_id("never labelled"): "never labelled"}
    assert dry["refused"] == {P.text_id("a text the model refused"): 1}
    assert P.answers(store)[P.text_id("a text the model refused")] == "content_filter"
    assert pq.read_table(path)["pushback"].to_pylist() == [None, None, None, None, True, None]   # nothing written
    done = P.apply([path], labels)
    assert pq.read_table(path)["pushback"].to_pylist() == [True, False, None, None, None, None]
    assert done["counts"]["files written"] == 1 and pq.read_table(path).schema == SCHEMA
    again = P.apply([path], labels)
    assert "files written" not in again["counts"] and "differ" not in again["counts"]

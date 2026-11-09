"""upload_checks.py on two small tiers: a clean pair passes; each fault the checks exist for fails its own check.

    python -m pytest tests/test_upload_checks.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytest.importorskip("duckdb")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release" / "upload_checks.py"


def _put(path: Path, cols: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(cols), str(path))


def _tiers(root: Path, *, both: bool = False, orphan: bool = False, text: bool = False, extra: bool = False,
           swe_unknown: bool = False, a_twice: bool = False, cjk: bool = False, no_messages: bool = False,
           models: int = 0, one_prose: str = "") -> tuple[Path, Path]:
    a, b = root / "a", root / "b"
    _put(a / "data" / "entire" / "part-000.parquet",
         {"session_key": ["entire:1", "entire:2", "dataclaw:9"], "duplicate_of": [None, None, "entire:1"]})
    if a_twice:   # the same kept session in a second file of tier A
        _put(a / "data" / "entire" / "part-001.parquet", {"session_key": ["entire:2"], "duplicate_of": [None]})
    if not no_messages:
        _put(a / "conversations" / "entire" / "part-000.parquet",
             {"session_key": ["entire:1", "entire:2", "dataclaw:9"] + (["entire:404"] if orphan else [])})
    _put(a / "conversations_subagents" / "entire" / "part-000.parquet", {"session_key": ["entire:2"]})
    _put(a / "work_labels" / "sessions.parquet", {"session_key": ["entire:1"]})
    _put(a / "attribution" / "entire.parquet", {"session_key": ["entire:2"]})
    url = "https://github.com/o/r/blob/abc/.claude/session.jsonl"
    _put(b / "pointers.parquet", {"session_key": ["swe-chat-v2:s1", "specstory:7"] + (["entire:2"] if both else []),
                                  "source": ["swe-chat-v2", "specstory"] + (["entire"] if both else []),
                                  "session_id": ["s1", "7"] + (["2"] if both else []),
                                  "found_url": [url, "fix the login page so that it works on phones" if text else url]
                                  + ([url] if both else []),
                                  "agent": ["claude-code", "请把登录页面修好让它在手机上也能正常工作" if cjk else "cursor"]
                                  + (["codex"] if both else []),
                                  "models": [["an open model released by a lab in a month of a year"] * models, []]
                                  + ([[]] if both else [])})
    _put(b / "labels" / "conversations" / "specstory" / "part-000.parquet",
         {"session_key": ["specstory:7"], "actor": ["human"]})
    if one_prose:   # one message's text among 150 labels: one string is enough to fail
        _put(b / "labels" / "conversations" / "specstory" / "part-009.parquet",
             {"session_key": ["specstory:7"] * 150, "actor": ["human"] * 149 + [one_prose]})
    _put(b / "labels" / "swe_chat_prompts.parquet", {"session_id": ["s1", "s404" if swe_unknown else "s1"],
                                                     "written_by": ["person", "program"]})
    _put(b / "work_labels" / "sessions.parquet", {"session_key": ["specstory:7"]})
    _put(b / "attribution" / "entire.parquet", {"session_key": ["swe-chat-v2:s1"]})
    (b / "changelog.json").write_text("{}")
    (b / "repositories.txt").write_text("o/r\n")
    (b / "work_labels" / "meta.json").write_text("{}")
    if extra:
        (b / "notes.md").write_text("a note")
    return a, b


def _run(a: Path, b: Path, expected: int) -> tuple[int, dict]:
    p = subprocess.run([sys.executable, str(SCRIPT), str(a), str(b), str(expected)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout) if p.stdout.strip().startswith("{") else {"stderr": p.stderr}


def test_a_clean_pair_passes(tmp_path: Path) -> None:
    code, out = _run(*_tiers(tmp_path), 4)
    assert code == 0 and out["failed"] == [], out
    assert out["every session once"]["together"] == 4 and out["every session once"]["tier A copy rows"] == 1


def test_swe_chat_prompt_labels_are_counted_not_joined(tmp_path: Path) -> None:
    code, out = _run(*_tiers(tmp_path, swe_unknown=True), 4)
    assert code == 0 and out["the tables join"]["SWE-chat prompt labels"] == {"sessions named": 2, "of them tier B's": 1}


@pytest.mark.parametrize("fault, expected, failed", [
    ({"both": True}, 4, "every session once"),
    ({}, 5, "every session once"),
    ({"orphan": True}, 4, "join: messages"),
    ({"text": True}, 4, "tier B holds no text"),
    ({"extra": True}, 4, "tier B holds no text"),
    ({"a_twice": True}, 4, "every session once"),
    ({"cjk": True}, 4, "tier B holds no text"),
    ({"no_messages": True}, 4, "join: messages"),
    ({"models": 101}, 4, "tier B holds no text"),
    ({"one_prose": "请把登录页面修好让它在手机上也能正常工作"}, 4, "tier B holds no text"),
    ({"one_prose": "/goal 请把登录页面修好让它在手机上也能正常工作 src/app.js"}, 4, "tier B holds no text"),
])
def test_each_fault_fails_its_check(tmp_path: Path, fault: dict, expected: int, failed: str) -> None:
    code, out = _run(*_tiers(tmp_path, **fault), expected)
    assert code == 1 and failed in out["failed"], out


def test_relative_paths_and_a_few_model_descriptions_pass(tmp_path: Path, monkeypatch) -> None:
    a, b = _tiers(tmp_path, models=100)
    _put(b / "labels" / "conversations" / "specstory" / "part-001.parquet",   # a file named by a title in Chinese
         {"session_key": ["specstory:7"], "actor": ["human"],
          "path": ["o/r/.specstory/history/2025-06-01_请把登录页面修好让它在手机上也能正常工作.md"]})
    p = subprocess.run([sys.executable, str(SCRIPT), "a", "b", "4"], capture_output=True, text=True, cwd=tmp_path)
    out = json.loads(p.stdout)
    assert p.returncode == 0 and out["failed"] == [], out
    assert out["the tables join"]["messages"]["files"] == 1

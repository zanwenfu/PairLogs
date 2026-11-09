"""Counts read from a Claude Code transcript: who typed what.

    python -m pytest tests/test_facts.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect import facts  # noqa: E402


def _write(tmp_path: Path, entries: list[dict]) -> Path:
    for n, e in enumerate(entries):
        e.setdefault("uuid", f"u{n}")
        e.setdefault("parentUuid", f"u{n - 1}" if n else None)
        e.setdefault("timestamp", f"2026-10-03T10:{n:02d}:00Z")
    path = tmp_path / "full.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def _user(content, **kw) -> dict:
    return {"type": "user", "message": {"role": "user", "content": content}, **kw}


def _assistant(text: str, n: int) -> dict:
    return {"type": "assistant", "message": {"id": f"m{n}", "model": "claude-x", "role": "assistant",
                                             "content": [{"type": "text", "text": text}]}}


def test_a_command_counts_once_and_a_pasted_image_counts(tmp_path: Path) -> None:
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
    path = _write(tmp_path, [
        _user("fix the failing test"),
        _assistant("on it", 1),
        _user("<command-message>review is running…</command-message>\n<command-name>/review</command-name>"),
        _user([{"type": "text", "text": "Review the current diff for bugs and missing tests."}], isMeta=True),
        _assistant("reviewing", 2),
        _user([image]),
        _assistant("I see the screenshot", 3),
    ])
    got = facts.claude_code([path])
    assert got["n_user_messages"] == 3, got  # the prompt, /review once, the pasted image
    assert got["n_user_entries"] == 4


def test_a_prompt_command_alone_counts_once(tmp_path: Path) -> None:
    path = _write(tmp_path, [
        _user("<command-message>review is running…</command-message>\n<command-name>/review</command-name>"),
        _user([{"type": "text", "text": "Review the current diff for bugs and missing tests."}], isMeta=True),
        _assistant("reviewing", 1),
    ])
    assert facts.claude_code([path])["n_user_messages"] == 1


def test_an_image_pasted_alone_counts(tmp_path: Path) -> None:
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
    path = _write(tmp_path, [_user([image]), _assistant("I see it", 1)])
    assert facts.claude_code([path])["n_user_messages"] == 1


def test_built_in_commands_tool_results_and_meta_images_are_not_typed(tmp_path: Path) -> None:
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
    path = _write(tmp_path, [
        _user("<command-name>/clear</command-name>\n<command-message>clear</command-message>\n<command-args></command-args>"),
        _user("<local-command-stdout></local-command-stdout>"),
        _user("hello"),
        _assistant("hi", 1),
        _user([{"type": "tool_result", "tool_use_id": "t1", "content": [image]}]),
        _user([image], isMeta=True),
        _user([]),
    ])
    got = facts.claude_code([path])
    assert got["n_user_messages"] == 1, got  # only "hello"

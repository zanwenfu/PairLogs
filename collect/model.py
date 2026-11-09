"""One session's row: its raw files and its labels.

Every label states a fact read from the files or from where they were found;
none is a judgement. A label a source cannot give is None, never a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .util import sha256_file, sha256_files, utc_now

# Every label a published row carries, in the published order.
LABELS = (
    "source", "source_id", "dedup_key", "found_url", "found_commit", "fetched_at", "collector_version",
    "content_sha256", "repo", "repo_url", "repo_license", "repo_language", "agent", "agent_version", "models",
    "started_at", "ended_at", "file_format", "n_user_entries", "n_user_messages", "n_agent_messages", "n_tool_calls",
    "has_tool_results", "has_timestamps", "bytes", "duplicate_of", "also_in",
    "redactions",
)


@dataclass
class RawFile:
    """A file as its source served it. `path` is its name within the session; `local` where it is on disk."""

    path: str
    local: Path
    size: int = 0
    sha256: str = ""

    @classmethod
    def at(cls, path: str, local: Path) -> "RawFile":
        return cls(path=path, local=local, size=local.stat().st_size, sha256=sha256_file(local))

    def as_json(self) -> dict:
        return {"path": self.path, "local": str(self.local), "bytes": self.size, "sha256": self.sha256}


@dataclass
class Session:
    source: str
    source_id: str
    files: list[RawFile]
    found_url: str | None = None
    found_commit: str | None = None
    repo: str | None = None
    repo_url: str | None = None
    repo_license: str | None = None
    repo_language: str | None = None
    agent: str | None = None
    agent_version: str | None = None
    file_format: str | None = None
    dedup_key: str | None = None
    facts: dict = field(default_factory=dict)  # counts, times, models: from `facts`
    extra: dict = field(default_factory=dict)  # source-specific facts kept with the row, not published as labels

    def row(self) -> dict:
        """The stored row: every label, the files, and the content digest that decides new, changed or same."""
        files = sorted(self.files, key=lambda f: f.path)
        digest = sha256_files([f.local for f in files]) if files else None
        row = {label: None for label in LABELS}
        row.update({
            "source": self.source, "source_id": self.source_id,
            "dedup_key": self.dedup_key or (f"sha256:{digest}" if digest else None),
            "found_url": self.found_url, "found_commit": self.found_commit, "fetched_at": utc_now(),
            "collector_version": __version__, "content_sha256": digest, "repo": self.repo, "repo_url": self.repo_url,
            "repo_license": self.repo_license, "repo_language": self.repo_language, "agent": self.agent,
            "agent_version": self.agent_version, "file_format": self.file_format, "bytes": sum(f.size for f in files),
            "also_in": [], "redactions": None,
        })
        for key in ("models", "started_at", "ended_at", "n_user_entries", "n_user_messages", "n_agent_messages",
                    "n_tool_calls", "has_tool_results", "has_timestamps"):
            if key in self.facts:
                row[key] = self.facts[key]
        if self.facts.get("agent_version") and not row["agent_version"]:
            row["agent_version"] = self.facts["agent_version"]
        row["files"] = [f.as_json() for f in files]
        row["extra"] = self.extra
        return coerce(row)


INTS = ("n_user_entries", "n_user_messages", "n_agent_messages", "n_tool_calls", "bytes")
BOOLS = ("has_tool_results", "has_timestamps")
STRS = ("found_url", "found_commit", "repo", "repo_url", "repo_license", "repo_language", "agent", "agent_version",
        "started_at", "ended_at", "file_format", "dedup_key", "duplicate_of")
# One name per agent, whatever its source wrote: `claude-code` and `Claude Code`, `opencode`, `Opencode` and
# `OpenCode`, `Codex CLI` and `Codex` were each a group of their own (2026-10-05; the study draws by agent). A source's
# own word for "not known" is None. Names not here are kept as written (Oh My Pi, Cursor CLI: other products).
AGENT_NAMES = {"claude code": "Claude Code", "claude-code": "Claude Code", "claude_code": "Claude Code",
               "claude": "Claude Code", "codex": "Codex", "codex cli": "Codex", "opencode": "OpenCode",
               "gemini": "Gemini CLI", "gemini cli": "Gemini CLI", "copilot cli": "Copilot CLI", "cursor": "Cursor",
               "factory ai droid": "Factory AI Droid", "factory droid": "Factory AI Droid",
               "factory droid cli": "Factory AI Droid", "pi": "Pi", "pi coding agent": "Pi", "openhands": "OpenHands",
               "aider": "Aider", "devin": "Devin", "unknown": None, "other": None, "none": None, "null": None, "": None}


def canonical_agent(name) -> str | None:
    if name is None:
        return None
    key = " ".join(str(name).split()).lower()
    return AGENT_NAMES[key] if key in AGENT_NAMES else " ".join(str(name).split())


def coerce(row: dict) -> dict:
    """Every label as the type it is published as, so one odd value (Copilot's integer version, say) cannot stop a
    build at its last step."""
    for k in INTS:
        v = row.get(k)
        row[k] = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    for k in BOOLS:
        v = row.get(k)
        row[k] = bool(v) if isinstance(v, bool) else None
    for k in STRS:
        v = row.get(k)
        row[k] = None if v is None else str(v)
    row["models"] = [str(m) for m in (row.get("models") or []) if m is not None]
    row["agent"] = canonical_agent(row.get("agent"))
    return row

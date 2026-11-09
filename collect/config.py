"""Where data and state live, and the credentials, read once from the environment.

Data and state default to ``./data`` and ``./state``; ``ERRATA_DATA_ROOT`` and
``ERRATA_STATE_ROOT`` move them (to a data disk, say). Credentials come from the
environment or from ``~/.config/errata-data/env`` (``KEY=value`` lines). They
are never printed: the fields that hold them are left out of every repr.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_FILE = Path.home() / ".config" / "errata-data" / "env"
HF_REPO = "zanwenfu/errata-data"


def _env_file(path: Path = ENV_FILE) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text()
    except (FileNotFoundError, PermissionError):
        return out
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):  # a shell-style line reads the same
            line = line[len("export "):].strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip("'\"")
    return out


@dataclass(frozen=True)
class Config:
    data: Path
    state: Path
    github_token: str | None = field(default=None, repr=False)
    hf_token: str | None = field(default=None, repr=False)
    hf_repo: str = HF_REPO
    hf_public: bool = False  # private unless ERRATA_HF_PUBLIC=1: the owner's choice of 2026-10-03, private first

    @property
    def raw(self) -> Path:
        """Unredacted files as fetched. Never published, never leaves the run machine."""
        return self.data / "raw"

    @property
    def copied(self) -> Path:
        """Copied datasets' files, as downloaded."""
        return self.data / "copied"

    @property
    def build(self) -> Path:
        """Versions built for publishing: redacted files, tables, the card. ``ERRATA_BUILD_ROOT`` moves it: a build
        removes the older unpublished builds beside it, so the weekly run and a test each keep their own."""
        import os

        return Path(os.environ["ERRATA_BUILD_ROOT"]).expanduser() if os.environ.get("ERRATA_BUILD_ROOT") else self.data / "build"

    @property
    def ledger(self) -> Path:
        return self.state / "ledger.sqlite"


def load(env: dict[str, str] | None = None) -> Config:
    env = {**_env_file(), **os.environ} if env is None else env
    data = Path(env.get("ERRATA_DATA_ROOT") or "data").expanduser()
    state = Path(env.get("ERRATA_STATE_ROOT") or "state").expanduser()
    return Config(data=data, state=state, github_token=env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or None,
                  hf_token=env.get("HF_TOKEN") or None, hf_repo=env.get("ERRATA_HF_REPO") or HF_REPO,
                  hf_public=(env.get("ERRATA_HF_PUBLIC") or "").strip().lower() in ("1", "true", "yes"))

"""The sources, and what every source is given to run with."""

from __future__ import annotations

from concurrent.futures import Executor
from dataclasses import dataclass, field
from typing import Callable

from ..config import Config
from ..github import GitHub
from ..ledger import Ledger


@dataclass
class Context:
    config: Config
    ledger: Ledger
    github: GitHub | None
    cpu: Executor  # a process pool for parsing and hashing
    log: Callable[[str], None] = print
    dry_run: bool = False
    workers: int = 16  # network fetches in flight per source
    only: tuple[str, ...] = ()  # restrict a source to these repositories or datasets (a sample, a test)
    limit: int = 0  # at most this many repositories or datasets per source (0: all)
    stats: dict = field(default_factory=dict)


# Name, then the module that collects it. Each module has `run(ctx) -> dict`.
SCRAPED = ("entire", "specstory", "aider")
COPIED = ("swe-chat-v2", "dataclaw", "openhands-feedback")
ALL = SCRAPED + COPIED


def runner(name: str):
    if name == "entire":
        from . import entire
        return entire.run
    if name == "specstory":
        from . import specstory
        return specstory.run
    if name == "aider":
        from . import aider
        return aider.run
    if name in COPIED:
        from . import hfcopy
        return lambda ctx: hfcopy.run(ctx, name)
    raise KeyError(name)

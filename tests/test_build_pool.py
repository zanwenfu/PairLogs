"""collect.build._Pool and memory(): the build's workers started afresh between stages, and memory logged at each
(a full build ran out of its 160 GB writing the sessions, 2026-10-26)."""

import os
import sys

import pytest

from collect import build


def _pid(_):
    return os.getpid()


def test_renew_starts_new_workers_and_ends_the_old_ones(tmp_path):
    logged = []
    with build._Pool(2) as pool:
        before = set(pool.map(_pid, range(20)))
        pool.renew("the next stage", logged.append)
        if sys.platform.startswith("linux"):   # the old workers have ended before the next stage starts
            assert not any(os.path.exists(f"/proc/{p}") for p in before)
        after = set(pool.map(_pid, range(20)))
        assert pool.submit(_pid, 0).result() in after
    assert before and after and not before & after
    assert logged and logged[0].startswith("build: workers started afresh before the next stage; memory: ")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_memory_counts_this_process_and_its_workers():
    with build._Pool(2) as pool:
        list(pool.map(_pid, range(4)))
        got = build.memory()
    assert got.startswith("this process ") and " processes under it " in got
    n = int(got.split(" GB, ")[1].split(" processes")[0])
    assert n >= 2   # the two workers (and the fork server)

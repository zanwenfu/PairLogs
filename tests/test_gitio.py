"""collect.gitio: the bare clones a source fetches into."""
import subprocess

from collect import gitio


def test_a_fetch_clone_never_cleans_up_in_the_background(tmp_path):
    # git's own gc, set off by a fetch's 51st pack, rewrote `shallow` under the next fetch of a repository with 55,006
    # checkpoints: every fetch into the clone must run with no clean-up beside it.
    repo = tmp_path / "r.git"
    gitio.bare_promisor(repo, "https://github.com/o/r")
    got = lambda key: subprocess.run(["git", "config", "--get", key], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert got("gc.auto") == "0"
    assert got("maintenance.auto") == "false"
    assert got("remote.origin.promisor") == "true" and got("remote.origin.partialclonefilter") == "blob:none"


def _repo_with_blob(tmp_path, data: bytes):
    repo = tmp_path / "src"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f").write_bytes(data)
    oid = subprocess.run(["git", "hash-object", "-w", "f"], cwd=repo, capture_output=True, text=True,
                         check=True).stdout.strip()
    return repo, oid


def test_a_file_named_near_the_disk_limit_is_written(tmp_path):
    # A SpecStory history file named in Chinese, 240-odd bytes, failed with its ".part" name added (2026-10-25).
    repo, oid = _repo_with_blob(tmp_path, b"hello")
    name = "2026-04-13_06-16-02Z-" + "添" * 76 + ".md"     # 21 + 228 + 3 = 252 bytes; with ".part", 257
    assert 250 < len(name.encode()) <= 255
    dest = tmp_path / "out" / name
    assert gitio.write_blobs(repo, [(oid, dest)]) == 5 and dest.read_bytes() == b"hello"
    assert [p.name for p in dest.parent.iterdir()] == [name]   # no part file left


def test_closing_the_reader_part_way_through_an_object_does_not_wait(tmp_path):
    # An error mid-object left cat-file writing into a full pipe; close() waited 60 s and raised (DULASlee/hxlot).
    import time
    repo, oid = _repo_with_blob(tmp_path, b"x" * (8 << 20))
    reader = gitio._Reader(repo)
    assert reader.open(oid) == 8 << 20
    t = time.monotonic()
    reader.close()
    assert time.monotonic() - t < 10

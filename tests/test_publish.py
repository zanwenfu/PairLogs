"""Publishing makes the repository private, and reads it back as private, before anything is uploaded; public and
gated only when asked for."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from collect import publish
from collect.config import Config


class FakeApi:
    def __init__(self, *, starts_public=False, stays_public=False):
        self.private = None if not starts_public else False
        self.stays_public, self.gated, self.calls = stays_public, False, []

    def create_repo(self, repo, repo_type, private, exist_ok):
        self.calls.append(("create", private))
        if self.private is None:
            self.private = private

    def update_repo_settings(self, repo_id, repo_type, private=None, gated=None):
        self.calls.append(("settings", private, gated))
        if private is not None and not self.stays_public:
            self.private = private
        if gated is not None:
            self.gated = gated

    def dataset_info(self, repo):
        return SimpleNamespace(private=bool(self.private), gated=self.gated)

    def upload_large_folder(self, **kw):
        self.calls.append(("upload",))

    def list_repo_files(self, repo, repo_type):
        return []

    def create_tag(self, *a, **kw):
        self.calls.append(("tag",))


def run(tmp_path: Path, monkeypatch, api: FakeApi, public: bool = False):
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", lambda token: api)
    monkeypatch.setattr("collect.check.passed", lambda ledger, v: True)
    v = tmp_path / "2026-10-03"
    v.mkdir()
    (v / "README.md").write_text("# card\n")
    ledger = SimpleNamespace(record_version=lambda *a, **k: None)
    cfg = Config(data=tmp_path, state=tmp_path, hf_token="t", hf_public=public)
    publish.upload(cfg, ledger, v, {"version": "2026-10-03", "failed_sources": [], "licences_looked_up": True}, log=lambda *a: None)


def test_a_new_repository_is_private_before_anything_is_uploaded(tmp_path, monkeypatch):
    api = FakeApi()
    run(tmp_path, monkeypatch, api)
    assert api.calls[0] == ("create", True) and ("upload",) in api.calls and api.gated is False


def test_an_existing_public_repository_is_made_private_or_nothing_is_uploaded(tmp_path, monkeypatch):
    api = FakeApi(starts_public=True)
    run(tmp_path, monkeypatch, api)
    assert ("settings", True, None) in api.calls and ("upload",) in api.calls
    stuck = FakeApi(starts_public=True, stays_public=True)
    (tmp_path / "b").mkdir()
    with pytest.raises(RuntimeError, match="not private"):
        run(tmp_path / "b", monkeypatch, stuck)
    assert ("upload",) not in stuck.calls


def test_public_and_gated_only_when_asked(tmp_path, monkeypatch):
    api = FakeApi()
    run(tmp_path, monkeypatch, api, public=True)
    assert api.calls[0] == ("create", False) and api.gated == "manual"


def test_private_unless_asked_for_public():
    from collect import config

    assert config.load({}).hf_public is False
    assert config.load({"ERRATA_HF_PUBLIC": "1"}).hf_public is True and config.load({"ERRATA_HF_PUBLIC": "no"}).hf_public is False

"""Publish a built version to the Hugging Face dataset, under a dated tag.

Only with `collect update --publish`, and the first version only after the owner's approval. The repository is
private (the owner's choice of 2026-10-03: a private dataset first): it is made private, and read back as private,
before anything is uploaded. With ERRATA_HF_PUBLIC=1 it is public but gated instead, each request approved by hand
(`gated="manual"`); the card's `extra_gated_*` fields ask who is asking and why. Files the new version no longer has
are deleted from the repository, so the tag is exactly the version built. Large uploads go through
`upload_large_folder`, which resumes after an interruption.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .ledger import Ledger


def upload(cfg: Config, ledger: Ledger, version_dir: Path, changelog: dict, log=print) -> None:
    from huggingface_hub import CommitOperationDelete, HfApi

    from .check import passed

    if not cfg.hf_token:
        raise RuntimeError("no Hugging Face token: set HF_TOKEN")
    if changelog.get("failed_sources", ["(no record)"]):
        raise RuntimeError(f"refused: {version_dir.name} was built while these sources failed: "
                           f"{changelog.get('failed_sources', '(no record: built before this check existed)')}; "
                           f"it would publish without data the last version had")
    if not changelog.get("licences_looked_up"):
        raise RuntimeError(f"refused: {version_dir.name} was built without looking up repository licences (no GitHub "
                           f"token); its card would show every licence as not looked up")
    if not passed(ledger, version_dir):
        raise RuntimeError(f"refused: {version_dir.name} has no passing check; run `collect check --version "
                           f"{version_dir.name}` first")
    api = HfApi(token=cfg.hf_token)
    repo, version = cfg.hf_repo, changelog["version"]
    if not cfg.hf_public:
        # Private, and read back as private, before anything is uploaded: an existing public repository is made
        # private, never written to as it is.
        api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
        if not api.dataset_info(repo).private:
            api.update_repo_settings(repo_id=repo, repo_type="dataset", private=True)
        if not api.dataset_info(repo).private:
            raise RuntimeError(f"refused: {repo} is not private after making it so; nothing uploaded")
        mode = "private"
    else:
        # The gate is set, and read back, while the repository holds nothing: no file is ever served ungated.
        api.create_repo(repo, repo_type="dataset", private=False, exist_ok=True)
        api.update_repo_settings(repo_id=repo, repo_type="dataset", gated="manual")
        gate = api.dataset_info(repo).gated
        if gate != "manual":
            raise RuntimeError(f"refused: {repo} reads gated={gate!r} after setting it to 'manual'; nothing uploaded")
        mode = "gated: manual"
    local = {str(p.relative_to(version_dir)) for p in version_dir.rglob("*")
             if p.is_file() and ".cache" not in p.relative_to(version_dir).parts}
    log(f"publish: {len(local):,} files to {repo} ({mode})")
    api.upload_large_folder(repo_id=repo, repo_type="dataset", folder_path=str(version_dir))
    remote = set(api.list_repo_files(repo, repo_type="dataset"))
    stale = sorted(f for f in remote - local if f != ".gitattributes")
    if stale:  # by exact path: a pattern could match more than it names
        api.create_commit(repo_id=repo, repo_type="dataset", operations=[CommitOperationDelete(path_in_repo=f)
                                                                         for f in stale],
                          commit_message=f"Version {version}: remove {len(stale)} files it no longer has")
    api.create_tag(repo, tag=f"v{version}", repo_type="dataset", tag_message=f"errata-data {version}", exist_ok=True)
    ledger.record_version(version, changelog, published=True)
    log(f"publish: {repo} tagged v{version}")

"""Seed a fresh run with a copied dataset from a published version, when its revision has not changed.

The first versions were built on a run machine since deleted, and its ledger with it; a fresh run would download every
copied dataset again, and SWE-chat v2 is gated. When the dataset's revision on Hugging Face is the one a published
version holds, this copies that version's files (a copy, never a link: the published folder must not change) into the
run's data folder and records them in the run's ledger as downloaded, so `collect update` finds them "same".

python scripts/seed_copied_from_version.py --version-dir V --name swe-chat-v2 --id SALT-NLP/SWE-chat
(ERRATA_DATA_ROOT and ERRATA_STATE_ROOT name the run's folders, as for `collect`.)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect import config  # noqa: E402
from collect.ledger import Ledger  # noqa: E402
from collect.sources.hfcopy import _card_text, _configs, decide, provenance_quote  # noqa: E402
from collect.util import safe_name, sha256_file  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version-dir", required=True)
    ap.add_argument("--name", required=True, help="the copied source, e.g. swe-chat-v2")
    ap.add_argument("--id", required=True, help="the Hugging Face dataset id, e.g. SALT-NLP/SWE-chat")
    args = ap.parse_args()
    from huggingface_hub import HfApi

    cfg = config.load()
    info = HfApi(token=cfg.hf_token).dataset_info(args.id)
    src = Path(args.version_dir) / "copied" / args.name / safe_name(args.id)
    import json

    published = json.loads((Path(args.version_dir) / "changelog.json").read_text())["copied_datasets"][args.name]
    if published.get("revision") != info.sha:
        print(f"refused: {args.id} is at {info.sha} on Hugging Face, the version holds {published.get('revision')}")
        return 2
    dest = cfg.copied / args.name / safe_name(args.id) / info.sha
    if dest.exists():
        print(f"refused: {dest} exists")
        return 2
    shutil.copytree(src, dest)   # a copy: the published folder stays as it is
    files = [{"path": str(p.relative_to(dest)), "local": str(p), "bytes": p.stat().st_size, "sha256": sha256_file(p)}
             for p in sorted(dest.rglob("*")) if p.is_file() and ".cache" not in p.relative_to(dest).parts]
    entry = decide(args.name, args.id, info)
    entry.update(files=files, provenance_quote=provenance_quote(_card_text(dest / "README.md")),
                 card_configs=_configs(dest / "README.md"), seeded_from=str(args.version_dir))
    Ledger(cfg.ledger).set_state(args.name, f"dataset:{args.id}", entry)
    print(f"seeded {args.name} {args.id} at {info.sha}: {len(files):,} files, "
          f"{sum(f['bytes'] for f in files) / 1e9:,.1f} GB; copied={entry.get('copied')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

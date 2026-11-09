"""`collect`: fetch what is new from every source, then build (and, when asked, publish) a dated version.

    collect update [--source NAME ...] [--dry-run] [--publish] [--only REPO ...] [--limit N] [--workers N]
    collect check --version V       read a built version back and scan it again; publishing needs it to pass
    collect publish --version V     publish a version already built and checked
    collect status

Sources run side by side, each fetching in parallel up to its service's limits;
parsing and hashing run in a process pool over every core. A run that finds
nothing new or changed builds and publishes nothing, and says so.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__, config
from .github import GitHub
from .ledger import Ledger
from .sources import ALL, COPIED, Context, runner
from .util import process_pool

_print_lock = threading.Lock()


def _version_name(text: str) -> str:
    """A version's name as `--name` takes it: a date, with `.N` after it (the form version_name gives)."""
    import re

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(\.[1-9]\d*)?", text):
        raise argparse.ArgumentTypeError(f"{text!r} is not a version name (YYYY-MM-DD, or YYYY-MM-DD.N)")
    return text


def log(msg: str) -> None:
    with _print_lock:
        print(f"{time.strftime('%H:%M:%S', time.gmtime())} {msg}", flush=True)


def _lock(cfg):
    """One run at a time: two runs would share the scan's working folders and the ledger."""
    import fcntl

    cfg.state.mkdir(parents=True, exist_ok=True)
    fh = open(cfg.state / "run.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("refused: another collect run holds the lock")
        return None
    return fh


def read_only_file(path) -> list[str]:
    """The repositories or datasets a file names, one a line; blank lines and `#` comments skipped."""
    return [x.strip() for x in Path(path).read_text().splitlines() if x.strip() and not x.lstrip().startswith("#")]


def update(args) -> int:
    import faulthandler
    import signal

    # `kill -USR1 <pid>` writes every thread's stack to the log and the run goes on: a run that seems stuck can be
    # looked at without stopping it.
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    if args.only_file:
        args.only = list(args.only or ()) + read_only_file(args.only_file)
    cfg = config.load()
    lock = _lock(cfg)
    if lock is None:
        return 2
    sources = [] if args.no_fetch else (args.source or list(ALL))
    unknown = [s for s in sources if s not in ALL]
    if unknown:
        log(f"refused: no source named {unknown}; the sources are {list(ALL)}")
        return 2
    ledger = Ledger(cfg.ledger)
    github = GitHub(cfg.github_token) if cfg.github_token else None
    if github is None and not args.no_fetch:
        if not args.only and any(s in ("entire", "specstory", "aider") for s in sources):
            # Without a token nothing is discovered and no licence is looked up: a full update would quietly find
            # nothing new and could publish what it already had.
            log("refused: no GitHub token (GITHUB_TOKEN); a full update of the GitHub sources needs one. "
                "Name repositories with --only to read them without one.")
            return 2
        log("no GitHub token: discovery and licence lookups are skipped; repositories named with --only are read")
    results: dict[str, dict] = {}
    with process_pool(args.cpu or os.cpu_count() or 4) as cpu:
        ctx_for = lambda: Context(config=cfg, ledger=ledger, github=github, cpu=cpu, log=log, dry_run=args.dry_run,
                                  workers=args.workers, only=tuple(args.only or ()), limit=args.limit)

        def one(name: str) -> tuple[str, dict]:
            t0 = time.time()
            try:
                stats = runner(name)(ctx_for())
            except Exception as e:  # one source failing must not take the others down; it is reported
                log(f"{name}: FAILED {type(e).__name__}: {str(e)[:300]}")
                return name, {"error": f"{type(e).__name__}: {str(e)[:300]}"}
            stats["seconds"] = round(time.time() - t0)
            log(f"{name}: done {json.dumps(stats, default=str)[:600]}")
            return name, stats

        with ThreadPoolExecutor(max(1, len(sources))) as pool:
            for name, stats in pool.map(one, sources):
                results[name] = stats
    # A source that failed whole, or a copied dataset that could not be read, keeps a version from being published:
    # it would publish without data the last version had. Repositories that failed one by one are reported as gaps.
    failed = [n for n, s in results.items() if "error" in s or (n in COPIED and s.get("errors"))]
    if args.dry_run:
        log("dry run: nothing fetched, built or published")
        print(json.dumps(results, indent=1, default=str))
        return 1 if failed else 0
    changed = sum(s.get("new", 0) + s.get("changed", 0) + s.get("recounted", 0) + s.get("relabeled", 0)
                  for s in results.values())
    if args.no_build:
        log(f"fetched; {changed:,} sessions new or changed; build skipped (--no-build)")
        return 1 if failed else 0
    from . import build, dedup

    dedup.run(ledger, log=log)
    if not changed and not args.rebuild and not args.no_fetch:
        log("nothing new: no version published")
        return 1 if failed else 0
    # With nothing fetched, the licences are the ones the rows hold from the last fetch: looked up if any row has one.
    looked_up = (any(r.get("repo_license") for r in ledger.rows() if r["source"] in ("entire", "specstory", "aider"))
                 if args.no_fetch else github is not None)
    results["_run"] = {"licences_looked_up": looked_up, "failed": failed}
    # Only the sessions new or changed since the version last published, when its build kept their fingerprints;
    # every session with --full (or with none published).
    last = ledger.last_published()
    base = ((cfg.build / last) if (last and not args.full and not build.STRICT and (cfg.build / last / build.INPUTS).exists())
            else None)   # strict redaction sweeps every file: every session
    log(f"build: {'incremental from ' + str(base) if base else 'every session'}")
    version_dir, changelog = build.version(cfg, ledger, results, log=log, base=base, name=args.name)
    from . import check

    # Read back as a reader gets it, and scanned again (an incremental version: what holds new text): a version
    # that fails is never published.
    if not check.run(cfg, ledger, version_dir, log=log, base=base)["ok"]:
        log(f"check FAILED: {version_dir} is not to be published; see the check's findings above")
        return 1
    if args.publish:
        if failed:
            log(f"refused to publish: these sources failed: {failed}")
            return 1
        from . import publish

        publish.upload(cfg, ledger, version_dir, changelog, log=log)
    else:
        log(f"built {version_dir}; not published (pass --publish to upload)")
    return 1 if failed else 0


def publish_version(args) -> int:
    """Publish a version already built (and reviewed), without building again."""
    cfg = config.load()
    lock = _lock(cfg)
    if lock is None:
        return 2
    version_dir = cfg.build / args.version
    if not (version_dir / "changelog.json").exists():
        log(f"refused: no built version {args.version} under {cfg.build}")
        return 2
    from . import publish

    publish.upload(cfg, Ledger(cfg.ledger), version_dir, json.loads((version_dir / "changelog.json").read_text()),
                   log=log)
    return 0


def check_version(args) -> int:
    """Check a version already built, from its published files: a fresh scan, and no value naming the run machine."""
    cfg = config.load()
    lock = _lock(cfg)
    if lock is None:
        return 2
    version_dir = cfg.build / args.version
    if not (version_dir / "changelog.json").exists():
        log(f"refused: no built version {args.version} under {cfg.build}")
        return 2
    from . import check

    return 0 if check.run(cfg, Ledger(cfg.ledger), version_dir, procs=args.procs, log=log)["ok"] else 1


def write_tiers(args) -> int:
    from . import tiers

    signals = json.loads(Path(args.owners).read_text()) if args.owners else None
    print(json.dumps(tiers.write(Path(args.version_dir), Path(args.out), log=log, signals=signals,
                                 swe_prompts=Path(args.swe_prompts) if args.swe_prompts else None), indent=1))
    return 0


def mask_names(args) -> int:
    from . import names

    print(json.dumps(names.write(Path(args.tier_dir), Path(args.out), log=log, procs=args.procs), indent=1))
    return 0


def mark_briefs(args) -> int:
    """Helper agents' sessions filed as sessions of their own: every typed row marked as another agent's text
    (collect/briefs.py): found in a built version, then, with --apply, rewritten there and in each folder named with
    --also (the version's tiers, when they were written first). Each folder's changelog records the pass."""
    from datetime import datetime, timezone

    from . import briefs

    version_dir = Path(args.version_dir)
    folders = [version_dir] + [Path(a) for a in args.also or ()]
    # one pass per folder: the changelog's helper_briefs is its record, and a second pass would overwrite it
    done = [str(f) for f in folders if (f / "changelog.json").exists()
            and "helper_briefs" in json.loads((f / "changelog.json").read_text())]
    if done:
        print(f"briefs were already marked in {', '.join(done)} (changelog.json says so); nothing changed",
              file=sys.stderr)
        return 1
    got = briefs.find(version_dir)
    out = {"found": len(got["rows"]), "sessions": sum(got["counts"].values()), "by_rule_and_source": got["counts"],
           "rows_by_rule_and_source": got["row_counts"], "opencode_helper_sessions": got["opencode_helper_sessions"],
           "dataclaw_haiku_only_sessions": got["dataclaw_haiku_only_sessions"], "brief_keys": got["brief_keys"]}
    changed_by = {f: briefs.apply(f, got["rows"], write=args.apply) for f in folders}
    # the digests again from the version's typed rows as they are now (after apply; on a dry run, as they were)
    fps = briefs.fingerprints(version_dir, {k for k, _, _ in got["rows"]}) if args.apply else {}
    for folder in folders:
        changed = changed_by[folder] + briefs.set_fingerprints(folder, fps, write=args.apply)
        out[str(folder)] = dict(changed)
        log_file = folder / "changelog.json"
        if args.apply and log_file.exists():
            c = json.loads(log_file.read_text())
            c["helper_briefs"] = {
                "rule": "every typed row of a helper agent's session filed as a session of its own (an OpenCode "
                        "session whose file names a parent session; a session whose first typed row matches an "
                        "orchestration call's prompt made in another session; a DataClaw Claude Code session that "
                        "names only Haiku models) is marked as another agent's text: typed_by_developer false, actor "
                        "peer, no pushback label",
                "rows": changed.get("rows marked another agent's", 0), "sessions": sum(got["counts"].values()),
                "sessions_by_rule_and_source": got["counts"], "rows_by_rule_and_source": got["row_counts"],
                "typed_fingerprints": {k: changed.get(k, 0) for k in ("fingerprints changed", "fingerprints removed")},
                "applied_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            tmp = log_file.with_name("changelog.json.briefs-tmp")
            tmp.write_text(json.dumps(c, indent=1, ensure_ascii=False))
            os.replace(tmp, log_file)
    print(json.dumps(out, indent=1))
    return 0


def status(args) -> int:
    cfg = config.load()
    ledger = Ledger(cfg.ledger)
    out = {"collector_version": __version__, "data": str(cfg.data), "state": str(cfg.state),
           "github_token": bool(cfg.github_token), "hf_token": bool(cfg.hf_token),
           "sessions": {s: ledger.count(s) for s in ALL}, "last_published": ledger.last_published()}
    print(json.dumps(out, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collect", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update", help="fetch what is new, build a version, and publish it when asked")
    u.add_argument("--source", action="append", choices=list(ALL), help="only this source (repeatable)")
    u.add_argument("--dry-run", action="store_true", help="find what is new and print the counts; fetch nothing")
    u.add_argument("--publish", action="store_true", help="upload the version built (the first needs approval)")
    u.add_argument("--only", action="append", help="only this repository or dataset (a sample, a test)")
    u.add_argument("--only-file", default=None,
                   help="a file of repositories or datasets, one a line (# comments): read only these, as --only "
                        "(tier B's repositories.txt rebuilds the sessions it points to)")
    u.add_argument("--limit", type=int, default=0, help="at most this many repositories or datasets per source")
    u.add_argument("--workers", type=int, default=16, help="network fetches in flight per source")
    u.add_argument("--cpu", type=int, default=0, help="processes for parsing and hashing (default: every core)")
    u.add_argument("--no-build", action="store_true", help="fetch only")
    u.add_argument("--rebuild", action="store_true", help="build a version even when nothing is new")
    u.add_argument("--full", action="store_true", help="read every session again, not only what moved since the "
                                                       "version last published (after a change to the readers)")
    u.add_argument("--no-fetch", action="store_true", help="fetch nothing: build and check a version from what is "
                                                            "collected (scripts/vm.sh run --no-fetch)")
    u.add_argument("--name", type=_version_name, default=None,
                   help="the version's name, a date (default: today's in UTC, `.2` and on after the first): a week's "
                        "version built again keeps its week's one name")
    u.set_defaults(fn=update)
    pb = sub.add_parser("publish", help="publish a version already built")
    pb.add_argument("--version", required=True, help="the version's name, its folder under data/build")
    pb.set_defaults(fn=publish_version)
    ck = sub.add_parser("check", help="check a version built: scan its published files again, look for machine paths")
    ck.add_argument("--version", required=True, help="the version's name, its folder under data/build")
    ck.add_argument("--procs", type=int, default=None,
                    help="worker processes (default: every core); fewer hold less in memory, and find the same")
    ck.set_defaults(fn=check_version)
    t = sub.add_parser("tiers", help="write a published version's release tiers: A full text where licences allow, "
                                     "B pointers for the rest")
    t.add_argument("--version-dir", required=True, help="a published version's folder (it holds changelog.json)")
    t.add_argument("--out", required=True, help="an empty folder; tier A goes to out/a, tier B to out/b")
    t.add_argument("--owners", help="JSON: owner login -> GitHub's profile facts (type, created, public_repos, "
                                     "university), for each tier's repositories.parquet (type only) and totals")
    t.add_argument("--swe-prompts", help="scripts/paper_e1/swe_prompts_who_typed.py's output, for tier B's "
                                          "labels/swe_chat_prompts.parquet")
    t.set_defaults(fn=write_tiers)
    nm = sub.add_parser("names", help="mask full names found in a tier's typed messages, wherever they occur in the "
                                      "same session (needs presidio-analyzer and spaCy's en_core_web_lg)")
    nm.add_argument("--tier-dir", required=True, help="a tier written by `collect tiers` (its a/ folder)")
    nm.add_argument("--out", required=True, help="an empty folder for the masked copy")
    nm.add_argument("--procs", type=int, default=1, help="processes for finding names (each loads the model once)")
    nm.set_defaults(fn=mask_names)
    br = sub.add_parser("briefs", help="mark every typed row of a helper agent's session, filed as a session of its "
                                       "own, as another agent's text (dry run without --apply)")
    br.add_argument("--version-dir", required=True, help="a built version's folder (it holds changelog.json)")
    br.add_argument("--also", action="append", help="another folder holding the same rows (a tier), rewritten too")
    br.add_argument("--apply", action="store_true", help="rewrite the rows; without it, only count")
    br.set_defaults(fn=mark_briefs)
    s = sub.add_parser("status", help="what is collected, and what was last published")
    s.set_defaults(fn=status)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

"""Read-only: the files a step after the build rewrote as text (the card and changelog), scanned as the build's last
round scans them (collect.scan.find_all: gitleaks + the strict formats, the not-secrets list applied), and searched for
the check's machine marks (collect.check.machine_marks). Prints counts only, never a finding's text.

python card_scan.py VERSION_DIR WORKDIR
"""
import random
import string
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from collect import check, config, scan


def main() -> int:
    V, WORK = Path(sys.argv[1]), Path(sys.argv[2])
    WORK.mkdir(parents=True, exist_ok=True)
    paths = [V / "README.md", V / "changelog.json"]
    # a control the scan must find, so a scan that ran on nothing cannot pass: a made-up token of GitHub's shape
    canary = WORK.parent / (WORK.name + "-canary") / "canary.txt"
    canary.parent.mkdir(parents=True, exist_ok=True)
    canary.write_text("token = ghp_" + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(36)) + "\n")
    with ProcessPoolExecutor(2) as pool:
        found = scan.find_all(paths + [canary], WORK, 2, pool)
    caught = sum(len(v) for k, v in found.items() if Path(k).name == "canary.txt")
    canary.unlink()
    found = {k: v for k, v in found.items() if Path(k).name != "canary.txt"}
    print("control caught:", caught)
    if not caught:
        print("card scan: FAIL (the control was not found: the scan did not run)")
        return 2
    n = {Path(k).name: len(v) for k, v in found.items()}
    rules = sorted({f.get("rule") or f.get("RuleID") or "?" for v in found.values() for f in v})
    marks = check.machine_marks(config.load())
    machine = {p.name: sum(p.read_text(encoding="utf-8").count(m) for m in marks) for p in paths}
    print("findings", n, "rules", rules, "| machine marks", len(marks), "found", machine)
    bad = sum(n.values()) + sum(machine.values())
    print("card scan:", "PASS" if bad == 0 else "FAIL")
    return 0 if bad == 0 else 1


if __name__ == "__main__":   # the pool starts its workers by forkserver (Python 3.14), which imports this file
    sys.exit(main())

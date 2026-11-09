"""Pre-flight check of the secret scan on the run machine, with the gitleaks installed there. No network.

    python tests/check_scanner.py

Synthetic secrets only, generated here (never real ones), in a temporary ledger and folder:
- a token in plain text, and the same kind of token base64-encoded (found only after decoding);
- a token at the very end of a 60 MB file, so no size limit skips it;
- the fields the rules exempt (Copilot's apiCallId, a thinking signature), which must not be flagged;
- a clean file, which must be recorded as scanned with no finding;
- multi-byte text before a token, a multi-line private key, tokens deep in a 260 KB line;
- files with NUL bytes and in UTF-16, staged as the build stages them;
- the build's own scan-until-clean loop over every staged file.
Exits 1 on any failure.
"""

from __future__ import annotations

import base64
import json
import random
import shutil
import string
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect import scan  # noqa: E402
from collect.build import _stage, until_clean  # noqa: E402
from collect.ledger import Ledger  # noqa: E402
from collect.util import process_pool, sha256_file  # noqa: E402

rng = random.Random(20261003)
token = lambda: "ghp_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36))
FAIL = []


def check(ok: bool, what: str) -> None:
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        FAIL.append(what)


def main() -> int:
    """Every check, in a temporary folder removed at the end. In a function: the build's worker processes import this
    script again, and must not run the checks themselves."""
    root = Path(tempfile.mkdtemp())
    try:
        return checks(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def checks(root: Path) -> int:
    files = {}
    plain, encoded_token, tail = token(), token(), token()
    files["plain.jsonl"] = '{"type":"user","message":{"content":"my token is ' + plain + ' ok"}}\n'
    files["encoded.jsonl"] = ('{"cmd":"echo ' + base64.b64encode(f"export GITHUB_TOKEN={encoded_token}".encode()).decode()
                              + ' | base64 -d"}\n')
    filler = ('{"type":"assistant","message":{"content":"' + "lorem ipsum " * 40 + '"}}\n')
    files["big.jsonl"] = filler * (60_000_000 // len(filler)) + '{"note":"' + tail + '"}\n'
    exempt_id = "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(500))
    files["exempt.jsonl"] = ('{"type":"assistant.message","data":{"apiCallId":"' + exempt_id[:40] + '"}}\n'
                             '{"type":"assistant","message":{"content":[{"type":"thinking","signature":"'
                             + exempt_id + '"}]}}\n')
    files["clean.jsonl"] = '{"type":"user","message":{"content":"run the tests"}}\n'
    pem_body = "\n".join("".join(rng.choice(string.ascii_letters + string.digits + "+/") for _ in range(64)) for _ in range(6))
    pem = "-----BEGIN RSA PRIVATE KEY-----\n" + pem_body + "\n-----END RSA PRIVATE KEY-----"
    files["pem.md"] = "# notes\n\nthe key was:\n\n" + pem + "\n\nend of notes\n"
    deep_plain, deep_encoded = token(), token()
    deep_b64 = base64.b64encode(f"export GITHUB_TOKEN={deep_encoded}".encode()).decode()
    filler = lambda n: "".join(rng.choice("abcdefgh ") for _ in range(n))
    files["long_line.jsonl"] = ('{"n":1}\n' + json.dumps({"content": filler(150_000) + " token " + deep_plain + " "
                                + filler(80_000) + " cmd " + deep_b64 + " " + filler(30_000)}) + "\n")
    late_plain, late_encoded = token(), token()
    late_b64 = base64.b64encode(f"export GITHUB_TOKEN={late_encoded}".encode()).decode()
    files["later_lines.jsonl"] = ('{"n":1}\n{"text":"日本語のテキスト 🙂 token ' + late_plain + ' end"}\n'
                                  '{"cmd":"é 🙂 echo ' + late_b64 + ' | base64 -d"}\n')
    paths = {}
    for name, text in files.items():
        p = root / "files" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        paths[sha256_file(p)] = p
    by_name = {p.name: sha for sha, p in paths.items()}

    ledger = Ledger(root / "ledger.sqlite")
    stats = scan.scan(ledger, paths, root / "work", procs=4, log=print)
    rules = stats["rules"]
    check(stats["scanned"] == len(files) and ledger.scanned(rules) >= set(paths), "every file is recorded as scanned")
    found = {name: ledger.findings(sha, rules) for name, sha in by_name.items()}
    check(any(f["secret"] == plain for f in found["plain.jsonl"]), "a plain token is found")
    # The columns' meaning, which cutting by span depends on: 1-based, end inclusive.
    f0 = next(f for f in found["plain.jsonl"] if f["secret"] == plain)
    line = files["plain.jsonl"].split("\n")[f0["line"] - 1]
    check(line[f0["start"] - 1:f0["end"]] == plain, f"columns are 1-based with an inclusive end ({f0['start']}-{f0['end']})")
    check(len(found["encoded.jsonl"]) >= 1, "a base64-encoded token is found after decoding")
    check(any(tail in f["secret"] for f in found["big.jsonl"]), "a token at the end of a 60 MB file is found")
    check(found["exempt.jsonl"] == [], f"apiCallId and a thinking signature are not flagged: {[f['rule'] for f in found['exempt.jsonl']]}")
    check(found["clean.jsonl"] == [], "a clean file has no finding")
    # Byte columns, one higher on every line after the first (what redact.byte_span relies on).
    fl = next((f for f in found["later_lines.jsonl"] if f["secret"] == late_plain), None)
    if fl:
        line2 = files["later_lines.jsonl"].split("\n")[1].encode()
        check(line2[fl["start"] - 2:fl["end"] - 1].decode() == late_plain,
              f"on a later line the columns are bytes and one higher ({fl['start']}-{fl['end']})")
    else:
        check(False, "the token after multibyte text on line 2 is found")
    content, counts = scan.redact_file(str(root / "files" / "later_lines.jsonl"), found["later_lines.jsonl"])
    check(late_plain not in content and late_b64 not in content and late_encoded not in content
          and "日本語のテキスト 🙂 token " + scan.MASK in content and "é 🙂 echo " + scan.MASK in content,
          f"tokens after multibyte text on lines 2 and 3, plain and encoded, are masked exactly ({counts['secrets']})")
    pk = [f for f in found["pem.md"] if f["rule"] == "private-key"]
    check(bool(pk) and all(f["end_line"] > f["line"] for f in pk), f"a multi-line private key is found across lines {[(f['line'], f['end_line']) for f in pk]}")
    content, counts = scan.redact_file(str(root / "files" / "pem.md"), found["pem.md"])
    check(pem_body.split("\n")[2] not in content and "-----BEGIN RSA PRIVATE KEY-----" not in content
          and content.startswith("# notes") and content.rstrip().endswith("end of notes"),
          f"the multi-line private key is masked whole, the text around it kept ({counts['secrets']})")
    for name in ("plain.jsonl", "encoded.jsonl"):
        content, counts = scan.redact_file(str(root / "files" / name), found[name])
        secret = plain if name == "plain.jsonl" else encoded_token
        encoded = base64.b64encode(f"export GITHUB_TOKEN={encoded_token}".encode()).decode()
        check(content is not None and secret not in content and encoded not in content and scan.MASK in content,
              f"{name}: the published text holds neither the token nor its encoding ({counts})")
    # Deep in a 260 KB line, where gitleaks' columns count from its 100 KB piece, not the line: placed by content.
    content, counts = scan.redact_file(str(root / "files" / "long_line.jsonl"), found["long_line.jsonl"])
    check(deep_plain not in content and deep_b64 not in content and json.loads(content.split("\n")[1])["content"].count(scan.MASK) == 2,
          f"tokens deep in a long line, plain and encoded, are masked where they are ({counts['secrets']})")
    # Files the build stages as text although they are not plain UTF-8, and one it must leave out.
    nul_token, nul_after, bom_token, nobom_token = token(), token(), token(), token()
    # A Google key behind doubly escaped quotes, deep in a long line: gitleaks' own rule wants a quote after it, and
    # decodes the escape only in some layouts of the text; the format pass finds it in every one.
    google = "AIza" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(35))
    odd = {  # NULs in the first bytes, inside a line, and a run of them with a token after it
        "escaped_google.jsonl": (json.dumps({"c": filler(140_000) + "{\\x22apiKey\\x22:\\x22" + google + "\\x22}"
                                             + filler(20_000)}) + "\n").encode(),
        "nul_tail.jsonl": b"\x00\x00" + ('{"type":"user","message":{"content":"key ' + nul_token + ' here"}}\n').encode()
                          + b'{"type":"assistant","msg":"a\x00b"}\n' + b"\x00" * 3000
                          + ('{"type":"user","message":{"content":"then ' + nul_after + '"}}\n').encode(),
        "utf16_bom.txt": ("the token is " + bom_token + "\n").encode("utf-16"),
        "utf16_no_bom.txt": ("the token is " + nobom_token + "\n").encode("utf-16-le"),
    }
    for name, data in odd.items():
        (root / "files" / name).write_bytes(data)
    # The build's own staging and scan-until-clean loop, over every file.
    stage = root / "stage"
    stage.mkdir()
    staged, left_out = [], []
    for name in list(files) + list(odd):
        raw = root / "files" / name
        path, _, is_text = _stage((str(raw), str(stage / (name + ".txt")), True, sha256_file(raw)))
        (staged if is_text else left_out).append(Path(path).resolve())
    check([p.name for p in left_out] == ["utf16_no_bom.txt.txt"],
          f"staged as text: NUL bytes in runs, UTF-16 with a mark; left out: UTF-16 without one ({[p.name for p in left_out]})")
    with process_pool(2) as pool:
        masked = until_clean(staged, root / "work2", pool, 2, log=print)
    left = {k: v for k, v in scan.find_all(staged, root / "work3", 2).items() if v}
    published = "".join(p.read_bytes().decode("utf-8", errors="replace") for p in staged)
    check(not left and masked and all(t not in published for t in (plain, encoded_token, tail, late_plain, deep_plain,
                                                                        nul_token, nul_after, bom_token, google)),
          f"the build's scan-until-clean leaves nothing a scan finds, NUL and UTF-16 files included ({len(masked)} files masked)")
    check(b"\x00" * 3000 in (stage / "nul_tail.jsonl.txt").read_bytes(), "the NUL bytes themselves are kept as fetched")
    again = scan.scan(ledger, paths, root / "work", procs=4, log=print)
    check(again["scanned"] == 0 and again["cached"] == len(files), "a second scan under the same rules reads nothing")
    print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

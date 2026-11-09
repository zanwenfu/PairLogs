"""The secret scan, and the redaction it implies, applied to every file before it is published.

**Scan.** gitleaks (a pinned release, `ERRATA_GITLEAKS` or `~/errata-data-tools/gitleaks`) runs with the rules in
`gitleaks.toml`: its own defaults, plus exceptions calibrated on real transcripts. Files are scanned by content
digest, once per rule set, in parallel shards over every core; each file's findings, none included, are recorded in
the ledger, so the build can check that every file it publishes was scanned under the rules in force.

**Redaction**, on the text published, never on the raw files kept on the run machine:
- every secret gitleaks found becomes `[REDACTED]`, wherever the same string occurs in the file. A secret gitleaks
  found only after decoding (base64, hex, percent-encoding) is cut by the span it reports;
- email addresses become `[REDACTED]`, except no-reply addresses, `git@` remotes and example domains;
- the user name inside a home path (`/Users/<name>/`, `/home/<name>/`, `C:\\Users\\<name>\\`) becomes `[REDACTED]`,
  except generic names (`runner`, `ubuntu`, `user`, ...).

Counts by kind (secrets by rule) go on each row and into the changelog.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .ledger import Ledger

RULES_FILE = Path(__file__).with_name("gitleaks.toml")
REDACTOR_VERSION = "4"  # change when the redaction below changes, so every file is redacted again
MASK = "[REDACTED]"


def gitleaks_path() -> str:
    env = os.environ.get("ERRATA_GITLEAKS")
    if env:
        return env
    local = Path.home() / "errata-data-tools" / "gitleaks"
    if local.exists():
        return str(local)
    found = shutil.which("gitleaks")
    if not found:
        raise RuntimeError("gitleaks is not installed: set ERRATA_GITLEAKS")
    return found


def rules_digest() -> str:
    """The rule set's identity: gitleaks' version, the rules file, and the redactor's version."""
    version = subprocess.run([gitleaks_path(), "version"], capture_output=True, text=True, timeout=60).stdout.strip()
    h = hashlib.sha256()
    h.update(version.encode())
    h.update(RULES_FILE.read_bytes())
    h.update(REDACTOR_VERSION.encode())
    return h.hexdigest()[:16]


def _run_shard(directory: Path, report: Path) -> list[dict]:
    p = subprocess.run([gitleaks_path(), "dir", str(directory), "--config", str(RULES_FILE), "--follow-symlinks",
                        "--max-target-megabytes", "0", "--report-format", "json", "--report-path", str(report),
                        "--no-banner", "--exit-code", "0", "--log-level", "error"],
                       capture_output=True, text=True, timeout=6 * 3600)
    if p.returncode != 0:
        raise RuntimeError(f"gitleaks failed on {directory}: {p.stderr[-400:]}")
    return json.loads(report.read_text() or "[]")


def find(paths: list[Path], workdir: Path, procs: int) -> dict[str, list[dict]]:
    """gitleaks over these files, in parallel shards: each file's findings, by its resolved path (none included)."""
    paths = sorted({p.resolve() for p in paths})
    out: dict[str, list[dict]] = {str(p): [] for p in paths}
    if not paths:
        return out
    shutil.rmtree(workdir, ignore_errors=True)
    shards = [[] for _ in range(max(1, min(procs, len(paths))))]
    sizes = [0] * len(shards)
    for path in sorted(paths, key=lambda p: -p.stat().st_size):
        i = sizes.index(min(sizes))
        shards[i].append(path)
        sizes[i] += path.stat().st_size
    dirs = []
    for i, shard in enumerate(shards):
        d = workdir / f"shard-{i:02d}"
        d.mkdir(parents=True)
        for n, path in enumerate(shard):
            (d / f"{n:07d}__{re.sub(r'[^A-Za-z0-9._-]', '_', path.name)[-80:]}").symlink_to(path)
        dirs.append(d)
    with ThreadPoolExecutor(len(dirs)) as pool:
        reports = list(pool.map(lambda d: _run_shard(d, d.with_suffix(".json")), dirs))
    for report in reports:
        for f in report:
            key = str(Path(f["File"]).resolve())
            if key not in out:
                raise RuntimeError(f"scan: a finding in a file not asked for: {f['File']}")
            out[key].append({"rule": f["RuleID"], "secret": f["Secret"], "line": f["StartLine"], "end_line": f.get("EndLine") or f["StartLine"], "match": f.get("Match"),
                             "start": f["StartColumn"], "end": f["EndColumn"],
                             "decoded": any(str(t).startswith("decoded") for t in (f.get("Tags") or []))})
    shutil.rmtree(workdir, ignore_errors=True)
    return out


# Tokens recognisable by their own format alone, whatever surrounds them. gitleaks' rules also want what follows a
# token (a quote, a space), and whether it decodes an escape there depends on the text around it: a Google API key
# behind `\\x22` escapes in an 18.7 MB OpenHands trajectory was found in 1 layout of the text out of 22 tried
# (2026-10-03). These are looked for in Python on every file of every scan, beside gitleaks.
STRICT = [
    ("google-api-key", r"AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"),
    ("github-token", r"gh[pousr]_[A-Za-z0-9]{36,255}(?![A-Za-z0-9])"),
    ("github-fine-grained-pat", r"github_pat_[A-Za-z0-9_]{82}(?![A-Za-z0-9_])"),
    ("gitlab-pat", r"glpat-[A-Za-z0-9_-]{20}(?![A-Za-z0-9_-])"),
    ("slack-token", r"xox[baprs]-[A-Za-z0-9-]{10,250}"),
    ("aws-access-key-id", r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])"),
    ("anthropic-api-key", r"sk-ant-(?:api|admin)[0-9]{2}-[A-Za-z0-9_-]{80,250}"),
    ("openai-api-key", r"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,200}T3BlbkFJ[A-Za-z0-9_-]{20,200}"),
    ("huggingface-token", r"hf_[A-Za-z]{34}(?![A-Za-z])"),
    ("stripe-live-key", r"(?:sk|rk)_live_[0-9A-Za-z]{24,250}"),
    ("npm-token", r"npm_[A-Za-z0-9]{36}(?![A-Za-z0-9])"),
]
# Not inside a longer word, but right after an escape is fine: `\\x22AIza...`, `\\u0022`, `%22`, `\\n`.
STRICT_PREFILTER = re.compile(rb"AIza|gh[pousr]_|github_pat_|glpat-|xox[baprs]-|AKIA|ASIA|sk-|hf_|_live_|npm_")
STRICT_RX = re.compile(r"(?:(?<![A-Za-z0-9_-])|(?<=\\[nrtbf])|(?<=\\x[0-9a-fA-F]{2})|(?<=\\u[0-9a-fA-F]{4})"
                       r"|(?<=%[0-9a-fA-F]{2}))(?:" + "|".join(f"(?P<{n.replace('-', '_')}>{rx})" for n, rx in STRICT) + ")")


def strict_findings(text: str, first_line: int = 1) -> list[dict]:
    """Every token of a STRICT format in the text, as findings like gitleaks' (rule `strict:<name>`). Lines are
    counted as the matches go (recounting from the start for each made the pass quadratic)."""
    out, line, pos = [], first_line, 0
    for m in STRICT_RX.finditer(text):
        line += text.count("\n", pos, m.start())
        pos = m.start()
        out.append({"rule": "strict:" + m.lastgroup.replace("_", "-"), "secret": m.group(0), "match": m.group(0),
                    "line": line, "end_line": line, "start": None, "end": None, "decoded": False})
    return out


def strict_file(path: str) -> tuple[str, list[dict]]:
    """The format pass over a file a line at a time: memory is one line's, however large the file."""
    out = []
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh, 1):
            if STRICT_PREFILTER.search(raw):
                out += strict_findings(raw.decode("utf-8", errors="surrogateescape"), n)
    return path, out


NOT_SECRETS_ENV = "ERRATA_NOT_SECRETS"
_not_secrets: dict[str, frozenset] = {}


def not_secrets() -> frozenset:
    """Texts the scan finds that are not secrets, by the sha256 of the text as the scan reports it (as written, and
    JSON-unescaped): the file ERRATA_NOT_SECRETS names, a digest a line; read once a process; none without it.
    gitleaks' generic rule takes names for keys, and a text found once is masked wherever its file holds it: version
    2026-10-04 masked 307 such texts (a dtype, model and class names, settings' names, paths) 131,870 times, while
    showing them open 1,687,936 times elsewhere (2026-10-07). Only digests are kept, never the texts."""
    path = os.environ.get(NOT_SECRETS_ENV) or ""
    if path not in _not_secrets:
        got = set()
        if path and Path(path).exists():
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                word = line.split()[0] if line.split() else ""
                if re.fullmatch(r"[0-9a-f]{64}", word):
                    got.add(word)
        _not_secrets[path] = frozenset(got)
    return _not_secrets[path]


KNOWN_SECRETS_ENV = "ERRATA_KNOWN_SECRETS"
_known_secrets: dict[str, frozenset] = {}


def known_secrets() -> frozenset:
    """Texts masked wherever a staged file or a row holds them, whether the scan finds them or not (build._sweep): the
    file ERRATA_KNOWN_SECRETS names, a text a line, kept on the run machine only; read once a process; none without
    it. gitleaks reads a big file in pieces, so a text found in one reading can be missed in the next, and version
    2026-10-04 masked texts by hand after its build (86 key-like texts where they stood open, the check's finds): a
    session read again keeps them masked (2026-10-09). A text is never logged."""
    path = os.environ.get(KNOWN_SECRETS_ENV) or ""
    if path not in _known_secrets:
        got = set()
        if path and Path(path).exists():
            text = Path(path).read_text(encoding="utf-8", errors="surrogateescape")
            got = {line for line in text.split("\n") if line.strip()}
        _known_secrets[path] = frozenset(got)
    return _known_secrets[path]


def secret_digest(f: dict) -> str:
    return hashlib.sha256(literal_of(f).encode("utf-8", errors="surrogateescape")).hexdigest()


def find_all(paths: list[Path], workdir: Path, procs: int, pool=None) -> dict[str, list[dict]]:
    """gitleaks (`find`) and the STRICT formats over these files: each file's findings, by its resolved path; but a
    finding whose text is listed as not a secret (not_secrets)."""
    out = find(paths, workdir, procs)
    keys = sorted(out)
    results = pool.map(strict_file, keys, chunksize=8) if pool is not None else map(strict_file, keys)
    for key, extra in results:
        out[key] += extra
    allowed = not_secrets()
    if allowed:
        out = {k: [f for f in fs if secret_digest(f) not in allowed] for k, fs in out.items()}
    return out


def scan(ledger: Ledger, files: dict[str, Path], workdir: Path, procs: int, log=print) -> dict:
    """Scan every file (content digest -> path) not yet scanned under the current rules, and record its findings."""
    rules = rules_digest()
    done = ledger.scanned(rules)
    todo = {sha: path for sha, path in files.items() if sha not in done}
    stats = {"rules": rules, "files": len(files), "cached": len(files) - len(todo), "scanned": len(todo),
             "findings": 0}
    if not todo:
        return stats
    shutil.rmtree(workdir, ignore_errors=True)
    shards = [[] for _ in range(max(1, procs))]
    sizes = [0] * len(shards)
    for sha, path in sorted(todo.items(), key=lambda kv: -kv[1].stat().st_size):  # largest first, to the lightest
        i = sizes.index(min(sizes))
        shards[i].append((sha, path))
        sizes[i] += path.stat().st_size
    dirs = []
    for i, shard in enumerate(shards):
        if not shard:
            continue
        d = workdir / f"shard-{i:02d}"
        d.mkdir(parents=True)
        for sha, path in shard:
            (d / f"{sha}__{re.sub(r'[^A-Za-z0-9._-]', '_', path.name)[-80:]}").symlink_to(path.resolve())
        dirs.append(d)
    log(f"scan: {len(todo):,} files ({sum(sizes) / 1e9:.2f} GB) in {len(dirs)} shards")
    results: dict[str, list] = {sha: [] for sha in todo}
    # gitleaks names a file by its link's target when it follows symlinks (checked by tests/check_scanner.py), and
    # by the link otherwise; either leads back to the file's digest.
    by_target = {str(path.resolve()): sha for sha, path in todo.items()}
    with ThreadPoolExecutor(len(dirs)) as pool:
        reports = list(pool.map(lambda d: _run_shard(d, d.with_suffix(".json")), dirs))
    for report in reports:
        for f in report:
            sha = by_target.get(str(Path(f["File"]).resolve())) or Path(f["File"]).name.split("__", 1)[0]
            if sha not in results:
                raise RuntimeError(f"scan: a finding in a file not asked for: {f['File']}")
            results[sha].append({"rule": f["RuleID"], "secret": f["Secret"], "line": f["StartLine"], "end_line": f.get("EndLine") or f["StartLine"], "match": f.get("Match"),
                                 "start": f["StartColumn"], "end": f["EndColumn"],
                                 "decoded": any(str(t).startswith("decoded") for t in (f.get("Tags") or []))})
            stats["findings"] += 1
    ledger.record_scan(rules, results)
    shutil.rmtree(workdir, ignore_errors=True)
    return stats


# -- redaction ------------------------------------------------------------------------------------
# An email's local part may not start right after a word character or a backslash, except right after a JSON escape
# (`\n`, `\t`, ... or `\uXXXX`): `\njohn@x.com` gives `\n[REDACTED]`, and `\n@app.get` (a decorator after an escaped
# newline) is no email at all. A domain ending in a file extension (`logo@2x.png`) is no email either. Measured on
# 2026-10-03: the first pattern, without these rules, masked 65,012 strings in 9,051 files, most of them decorators
# (`@app.get`, `@pytest.mark.asyncio`) and image names, and broke JSON after the escapes.
EMAIL = re.compile(r"(?:(?<![\w.%+\-\\])|(?<=\\[nrtbf])|(?<=\\u[0-9a-fA-F]{4}))"
                   # the local part may hold Markdown's escaped `\_` or `\.` (`\\_` once JSON-escaped): 8 addresses
                   # written `first\_last@...` were left whole on the sample (review, 2026-10-03)
                   r"((?:[A-Za-z0-9._%+-]|\\{1,2}[_.]|\\{1,8}u(?:00[89a-fA-F][0-9a-fA-F]|0[1-9a-fA-F][0-9a-fA-F]{2}|[1-9a-fA-F][0-9a-fA-F]{3})){1,64})(?:@|%40)([A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.([A-Za-z]{2,24}))"
                   r"(?!\w|-\w)")  # a dash may follow (`hi@x.com------`), not a dash and a word (`a@b.com-x`)
FILE_SUFFIXES = {"png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "avif", "json", "js", "mjs", "cjs", "ts",
                 "tsx", "jsx", "md", "py", "txt", "css", "scss", "html", "yml", "yaml", "toml", "lock", "map", "sh",
                 "rb", "go", "rs", "java", "kt", "swift", "vue", "svelte", "wasm", "pdf", "zip", "gz", "csv", "xml"}
KEEP_EMAIL_LOCAL = {"noreply", "no-reply", "git", "donotreply", "do-not-reply"}
KEEP_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "localhost")
# A home path's user name: any letters (any script), digits, `.`, `_`, `-`, ending at anything else.
# Inside a name, only an escape of a letter beyond ASCII (`J\u00f6rg`): an escaped `<` or quote (`\u003c`) ends it.
HOME_POSIX = re.compile(r"(/Users/|/home/)((?:[\w.-]|\\{1,8}u(?:00[89a-fA-F][0-9a-fA-F]|0[1-9a-fA-F][0-9a-fA-F]{2}|[1-9a-fA-F][0-9a-fA-F]{3})){1,64})(?=[^\w.-]|$)")
# Windows: any case (`c:\\users\\`), and the name ending at a separator, a quote, a prompt's `>`, punctuation, a line's
# end, or a space before a symbol (`C:\\Users\\jane && dir`); a name may hold spaces (`Alice Smith`).
HOME_WINDOWS = re.compile(r"([A-Za-z]:(?:\\\\|\\|/)(?i:users)(?:\\\\|\\|/))([\w. -]{1,64}?)"
                          r"(?=\\|/|\"|'|>|[,;:)\]}]|\r|\n|\s(?=[^\w\s.-])|$)")
# In JSON text a lone backslash starts an escape (`\n`, `\"`), and a path's separator is written `\\` (more where a
# JSON string holds JSON): read as a separator, `C:\\Users\nC:` took the `\n` of a line break for one and masked
# `nC` as a name, which broke the escape (two Entire transcripts stopped the first full build, 2026-10-03).
# A lone backslash that starts no valid escape is a separator too: Gemini CLI writes `C:\Users\alice` unescaped (the
# files facts._lenient exists for).
_JSON_SEP = r'(?:(?:\\\\)+|\\(?![\\"/bfnrtu])|\\(?=u(?![0-9a-fA-F]{4}))|/)'
HOME_WINDOWS_JSON = re.compile(r"([A-Za-z]:" + _JSON_SEP + r"(?i:users)" + _JSON_SEP + r")((?:[\w. -]|\\{1,8}u(?:00[89a-fA-F][0-9a-fA-F]|0[1-9a-fA-F][0-9a-fA-F]{2}|[1-9a-fA-F][0-9a-fA-F]{3})){1,64}?)"
                               r"(?=\\(?!\\*u(?:00[89a-fA-F][0-9a-fA-F]|0[1-9a-fA-F][0-9a-fA-F]{2}|[1-9a-fA-F][0-9a-fA-F]{3}))|/|\"|'|>|[,;:)\]}]|\r|\n|\s(?=[^\w\s.-])|$)")
# Claude Code names a project's folder after its path with `/` and `.` as `-` (`~/.claude/projects/-Users-jane-proj`);
# the same form appears in task-output paths. The name is masked up to its next dash, and names already seen in
# ordinary paths in the same text are masked whole in this form too.
HOME_DASHED = re.compile(r"(-Users-|-home-)([\w.]{1,64})(?=-|/|\"|'|$)")
GENERIC_USERS = {"runner", "ubuntu", "user", "users", "vscode", "node", "codespace", "codespaces", "ec2-user", "root",
                 "shared", "guest", "admin", "username", "yourname", "your-username", "your_username", "you", "me",
                 "default", "public", "www-data", "app", "dev", "developer", "coder", "linuxbrew", "jovyan",
                 "name", "example", "test", "redacted"}


def _keep_email(local: str, domain: str) -> bool:
    d = domain.lower()
    return (local.lower() in KEEP_EMAIL_LOCAL or any(d == k or d.endswith("." + k) for k in KEEP_EMAIL_DOMAINS)
            or d.rsplit(".", 1)[-1] in FILE_SUFFIXES)


def literal_of(f: dict) -> str:
    return (f.get("secret") or "").rstrip("\\")  # never strip the escape a quote after it depends on


# A character JSON escapes, as a run of backslashes then its letter: one backslash in a JSON string, more where a JSON
# string holds JSON (a tool's arguments stored as text: a quote two levels down is written `\\\"`, three backslashes).
_JSON_ESCAPE = {"\n": r"\\{1,8}n", "\t": r"\\{1,8}t", "\r": r"\\{1,8}r", '"': r'\\{1,15}"', "\\": r"\\{1,16}",
                "/": r"\\{1,15}/", "\b": r"\\{1,8}b", "\f": r"\\{1,8}f"}


def escape_tolerant(secret: str) -> re.Pattern:
    r"""The secret, each character as itself or as an escape a writer may have used for it (`\uXXXX`, `%XX`, JSON's
    `\n` and the like, at any depth a JSON string holding JSON gives it), so a secret the scan found only after
    decoding such escapes is found as written.

    A run of backslashes is one part, read whole and never given back (an atomic group). With a part per backslash,
    `\\` and `\\{1,16}` both read one backslash and a run could be shared among them in many ways, each tried in
    turn: a private key's copy cut short took exponential time in its lines (2^24 ways at its 24th line, over 30 s;
    review of rc6, 2026-10-04). The character after a run may have its escape's backslashes read by the run, so its
    escape is also accepted without them."""
    parts, after_run = [], False
    for m in re.finditer(r"\\+|[^\\]", secret):
        ch = m.group(0)
        if ch[0] == "\\":
            n = len(ch)
            parts.append(r"(?>(?:\\{1,8}u(?i:005c)|%(?:25){0,4}(?i:5c)|\\)" + f"{{{n},{16 * n}}})")
            after_run = True
            continue
        alts = [re.escape(ch)]
        cp = ord(ch)
        if cp < 0x10000:
            alts.append(r"\\{1,8}u(?i:" + f"{cp:04x}" + ")")
            if after_run:
                alts.append("u(?i:" + f"{cp:04x}" + ")")
        if cp < 0x80:   # percent-encoded, at any depth (`%253D` is `=` encoded twice, as gitleaks decodes it)
            alts.append("%(?:25){0,4}(?i:" + f"{cp:02x}" + ")")
        if ch in _JSON_ESCAPE:
            alts.append(_JSON_ESCAPE[ch])
            if after_run and _JSON_ESCAPE[ch][-1] != ch:
                alts.append(re.escape(_JSON_ESCAPE[ch][-1]))
        parts.append("(?:" + "|".join(alts) + ")")
        after_run = False
    return re.compile("".join(parts))


B64_RUN = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
HEX_RUN = re.compile(r"(?:[0-9a-fA-F]{2}){8,}")


def _decodings(run: str) -> list[str]:
    import base64
    import binascii

    out = []
    if HEX_RUN.fullmatch(run):
        try:
            out.append(bytes.fromhex(run).decode("utf-8", errors="replace"))
        except ValueError:
            pass
    for decode in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            out.append(decode(run + "=" * (-len(run) % 4)).decode("utf-8", errors="replace"))
        except (binascii.Error, ValueError):
            pass
    return out


def _holds(text: str, secret: str, depth: int) -> bool:
    if secret in text:
        return True
    if depth <= 0:
        return False
    for rx in (B64_RUN, HEX_RUN):
        for m in rx.finditer(text):
            if any(_holds(d, secret, depth - 1) for d in _decodings(m.group(0))):
                return True
    return False


def _encoded_spans(text: str, secret: str, depth: int = 3) -> list[tuple[int, int]]:
    """Base64 or hex runs in the text whose decoding (up to `depth` levels) holds the secret."""
    spans = []
    for rx in (B64_RUN, HEX_RUN):
        for m in rx.finditer(text):
            if any(_holds(d, secret, depth - 1) for d in _decodings(m.group(0))):
                spans.append(m.span())
    return spans


def _line_spans(text: str, first: int, last: int) -> list[tuple[int, int]]:
    """The spans of lines `first` to `last` (counted from 1, as gitleaks counts them: at `\n` only), each without its
    line break; none past the text's end."""
    spans, start, n = [], 0, 1
    while n <= last and start <= len(text):
        end = text.find("\n", start)
        end = len(text) if end < 0 else end
        if n >= first and end > start:
            spans.append((start, end))
        start, n = end + 1, n + 1
    return spans


def place(text: str, f: dict) -> list[tuple[int, int]]:
    r"""Where a finding's secret is in the text, by its content, never by the scan's columns: gitleaks counts columns
    from the start of the 100 KB piece of the file it was reading, not from the start of the line (measured on
    2026-10-03), so a column past the first piece of a long line points elsewhere.

    - A secret of six characters or more: every place it occurs.
    - A shorter one: where it occurs inside the scan's whole match (`curl -u user:pw`), so a short string is not
      masked everywhere it happens to occur.
    - One found only after decoding: as written with escapes (`\uXXXX`, `%XX`, JSON's), else the base64 or hex runs
      whose decoding holds it.
    """
    secret = literal_of(f)
    if not secret:
        return []
    if not f.get("decoded") and len(secret) >= 6 and secret in text:
        return [m.span() for m in re.finditer(re.escape(secret), text)]
    if not f.get("decoded") and "\n" in secret:
        # A secret over several lines met a line at a time (gitleaks' private-key match from a header into the next
        # records of a JSON-lines file): its first line, where the match starts, as the whole text would be masked.
        head = secret.split("\n", 1)[0]
        if len(head) >= 6 and head in text:
            return [m.span() for m in re.finditer(re.escape(head), text)]
    if not f.get("decoded") and secret in text:
        match = f.get("match") or ""
        spans = []
        if match and secret in match:
            # The whole match, or, for a match over several lines placed a line at a time (gitleaks' curl rules
            # allow five line breaks between `curl` and `-u user:pw`), the part of it on the secret's own line.
            for part in [match] + ([seg for seg in match.split("\n") if secret in seg] if "\n" in match else []):
                for m in re.finditer(re.escape(part), text):
                    i = text.find(secret, m.start(), m.end())
                    if i >= 0:
                        spans.append((i, i + len(secret)))
                if spans:
                    break
        return spans
    spans = [m.span() for m in escape_tolerant(secret).finditer(text)]
    return spans or _encoded_spans(text, secret)


# -- JSON text: masks stay inside strings, private keys by their own structure ------------------------------------
# A JSON string's tokens: an escape pair, or a quote. A JSON string holds no raw line break, so it lies on one line;
# its quotes are paired in one pass (a pattern for whole strings, tried again from each later quote of a line ending
# inside a string, took quadratic time: the partner's review of rc10, issue #12).
_STRING_TOKEN = re.compile(r'\\.|"')
_ESC_TOKEN = re.compile(r"\\u[0-9a-fA-F]{4}|\\.")
PEM_HEADER = re.compile(r"-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----", re.I)
# A key cut short or broken over lines (a terminal's wrap, a log's cut) matches none of the scan's rules, which want a
# key's whole length (Anthropic's: 93 characters and `AA`): version 2026-10-04 published such runs unmasked in 6 files
# (#17's evaluation and ours, 2026-10-05). Whatever its length, a run after a key's own prefix is masked, a
# placeholder's too: Anthropic's and OpenAI's newer keys, GitHub's tokens.
KEY_RUN = re.compile(r"(?:sk-ant-(?:api|admin|oat|sid)\d{2}-|sk-(?:proj|svcacct|admin)-)[A-Za-z0-9_\-]{20,}"
                     r"|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}")


def mask_key_runs(text: str) -> tuple[str, int]:
    """The text with every run after a key's prefix masked (KEY_RUN), and how many."""
    n = 0

    def one(m: re.Match) -> str:
        nonlocal n
        n += 1
        return MASK

    return KEY_RUN.sub(one, text), n
PEM_END = re.compile(r"-----END[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----", re.I)
# A line of a key, as a string or a line of text, after a diff's prefix, indentation, and a kept `\r`: a base64
# line, a PGP checksum (`=XXXX`), an armor header (`Version: ...`, `Proc-Type: 4,ENCRYPTED`, `DEK-Info: ...`).
# A blank line (PGP and encrypted RSA keys have one) is passed over. Review of 2026-10-03: a PGP key, an
# encrypted RSA key, and keys with Windows line endings stopped the walk at their first such line, and their
# bodies were published.
PEM_KEY_LINE = re.compile(r"[+\- ]?[ \t]*(?:[A-Za-z0-9+/]{4,}={0,2}|=[A-Za-z0-9+/]{4}|[A-Za-z][A-Za-z0-9-]*: .*)")
_LINE_EDGES = re.compile(r"^(?:\\[rnt]|\s)+|(?:\\[rnt]|\s)+$")  # spaces and escaped line ends, at either end
PEM_FOLLOW = 400  # strings or lines looked at after a header whose key has no end in the same string
PEM_REACH = 64 * 1024  # how far after a header its END is looked for (a PGP key with subkeys runs to ~30 KB)


def _key_line(content: str) -> str | None:
    """'line' for a line of a key, 'blank' for a blank one, None for anything else."""
    c = _LINE_EDGES.sub("", content.replace("\\/", "/"))
    if not c or c in "+- ":
        return "blank"
    return "line" if PEM_KEY_LINE.fullmatch(c) else None


# A short word of lowercase letters is no key's first line: base64 of random bytes mixes cases and digits. Right after a
# header whose key has no end, such a string is the record's next field (`"role": "user"`), and masking it made a
# developer's message an unknown author's (DataClaw, version 2026-10-04, 2026-10-07).
_WORD = re.compile(r"[a-z]{1,15}")


class JsonText:
    """A JSON or JSON-lines text's strings, read a line at a time and kept: where each string lies, whether it is an
    object's key, where its escapes are, and whether its line is a JSON value on its own (a record) or a line of a
    pretty-printed document."""

    def __init__(self, text: str):
        self.text = text
        self._tokens: dict[int, list[tuple[int, int, bool]]] = {}
        self._escapes: dict[int, list[tuple[int, int]]] = {}
        self._whole: dict[int, bool] = {}

    def line_of(self, i: int) -> tuple[int, int]:
        ls = self.text.rfind("\n", 0, i) + 1
        le = self.text.find("\n", i)
        return ls, (len(self.text) if le < 0 else le)

    def tokens(self, ls: int, le: int) -> list[tuple[int, int, bool]]:
        """The line's strings: (opening quote, closing quote, whether it is a key)."""
        got = self._tokens.get(ls)
        if got is None:
            got, t = [], self.text
            start = None
            for m in _STRING_TOKEN.finditer(t, ls, le):   # one pass: a quote outside an escape pair opens or closes
                if m.group(0) != '"':
                    continue
                if start is None:
                    start = m.start()
                    continue
                j = m.end()
                while j < le and t[j] in " \t\r":
                    j += 1
                got.append((start, m.start(), j < le and t[j] == ":"))
                start = None
            self._tokens[ls] = got
        return got

    def string_at(self, i: int):
        """The string whose content holds position i, as (open, close, is_key, line start, line end)."""
        ls, le = self.line_of(i)
        toks = self.tokens(ls, le)
        k = bisect.bisect_right(toks, (i, -1, True)) - 1
        if k >= 0 and toks[k][0] < i < toks[k][1]:
            return toks[k][0], toks[k][1], toks[k][2], ls, le
        return None, None, None, ls, le

    def whole(self, ls: int, le: int) -> bool:
        got = self._whole.get(ls)
        if got is None:
            try:
                json.loads(self.text[ls:le])
                got = True
            except ValueError:
                got = False
            self._whole[ls] = got
        return got

    def _esc(self, qo: int, qc: int) -> list[tuple[int, int]]:
        got = self._escapes.get(qo)
        if got is None:
            got = [m.span() for m in _ESC_TOKEN.finditer(self.text, qo + 1, qc)]
            self._escapes[qo] = got
        return got

    def floor(self, qo: int, qc: int, i: int) -> int:
        r"""i, moved back to the start of an escape (`\x`, `\uXXXX`) it falls inside."""
        esc = self._esc(qo, qc)
        k = bisect.bisect_right(esc, (i, len(self.text))) - 1
        return esc[k][0] if k >= 0 and esc[k][0] < i < esc[k][1] else i

    def ceil(self, qo: int, qc: int, i: int) -> int:
        """i, moved forward to the end of an escape it falls inside."""
        esc = self._esc(qo, qc)
        k = bisect.bisect_right(esc, (i, len(self.text))) - 1
        return esc[k][1] if k >= 0 and esc[k][0] < i < esc[k][1] else i


def json_spans(jt: JsonText, a: int, z: int) -> list[tuple[int, int]]:
    r"""A finding's span over JSON text, kept inside the string it starts in, and never cutting an escape.

    In JSON a string holds no raw line break and no unescaped quote, so a secret lies inside one string. A match
    running past its string's end is built across the text's structure: gitleaks' private-key pattern runs from a
    header to the next `KEY-----` at least 64 characters on, and masked whole such spans broke 5 of 4,197 published
    transcripts on the sample (2026-10-03). A key whose lines are strings of their own (an edit's patch lines) is
    masked by `pem_spans`, by its own structure. A span on a line that is not JSON at all is masked as text, within
    its line.
    """
    qo, qc, _, ls, le = jt.string_at(a)
    if qo is None:
        if not jt.whole(ls, le):
            return [(a, min(z, le))]
        raise RuntimeError("redact: a finding lies outside every string of a JSON line")
    start, end = jt.floor(qo, qc, a), jt.ceil(qo, qc, min(z, qc))
    return [(start, end)] if end > start else []


def _following_strings(jt: JsonText, after: int, ls: int, le: int):
    """The value strings after position `after`, as (open, close): on this line only for a JSON-lines record, on the
    next lines too in a pretty-printed document; at most PEM_FOLLOW of them, within PEM_REACH of `after`."""
    t, seen = jt.text, 0
    while True:
        for qo, qc, key in jt.tokens(ls, le):
            if qo <= after or key:
                continue
            if qo - after > PEM_REACH or seen >= PEM_FOLLOW:
                return
            seen += 1
            yield qo, qc
        if jt.whole(ls, le) or le >= len(t) or le - after > PEM_REACH:
            return
        ls = le + 1
        nxt = t.find("\n", ls)
        le = len(t) if nxt < 0 else nxt


def _pem_strings(jt: JsonText, after: int, ls: int, le: int) -> list[tuple[int, int]]:
    """After a header with no end in its own string, the key's other lines, each a string of its own (an edit's
    `structuredPatch` stores a file a line per string):

    - an END line within reach, before any other header: every string up to it, whatever it holds (armor headers, a
      blank line, a checksum: a key's lines are not all base64);
    - none (a placeholder header, or a key cut short): the strings that are lines of a key (`_key_line`), up to the
      first that is not, blank ones passed over."""
    following = list(_following_strings(jt, after, ls, le))
    t = jt.text
    for n, (qo, qc) in enumerate(following):
        if PEM_END.search(t, qo + 1, qc):
            return [(o + 1, c) for o, c in following[:n + 1] if c > o + 1]
        if PEM_HEADER.search(t, qo + 1, qc):
            # Another key starts first: this header has no END of its own (a placeholder), and the strings before
            # the next key are not masked; that key is masked from its own header (review of d42d7d7).
            following = following[:n]
            break
    out = []
    for qo, qc in following:
        kind = _key_line(t[qo + 1:qc])
        if kind is None:
            break
        if kind == "line" and not out and _WORD.fullmatch(_LINE_EDGES.sub("", t[qo + 1:qc])):
            break   # the next field of the record, not the key's first line
        if kind == "line":
            out.append((qo + 1, qc))
    return out


# A key's body before an END whose header is not within reach (a key printed in two parts, `head` then `tail`, over
# two rows or two tool results), read a line at a time back from the END. Lines break as written or escaped at any
# depth (`\r\n` as one), never at a space, so the words of a sentence before an END are no body.
PEM_BREAK = re.compile(r"(?:\\{1,8}r)?\\{1,8}n|\\{1,8}r|\r\n?|\n")
# What may come before a line of a key in tool output: a line number and its mark (the Read tool's `26→`, written
# `\u2192` too; `cat -n`'s tab; `grep -n`'s `26:`, and `key.pem-21-` around a match; Devin's `102|`; a box view's
# `  2 │`), a quote's `>`, indentation (a JSON `\t` too). The partner's review: tails read at an offset or grepped
# for their END were left whole.
_LINE_MARK = (r"(?:[ \t]*(?:[^\s:]{1,256}[:-])?\d{1,9}(?:\u2192|\\{1,8}u2192|\t|\\{1,8}t|[:-]"
              r"|[ \t]?(?:\||\u2502|\\{1,8}u2502))|[ \t]*(?:>[ \t]?)+)?(?:[ \t]|\\{1,8}t)*")
PEM_BODY_LINE = re.compile(_LINE_MARK + r"(?:[+-][ \t]*)?([A-Za-z0-9+/=]+)[ \t]*")   # a base64 run alone on its line
PEM_END_LEAD = re.compile(_LINE_MARK + r"[+-]?[ \t]*")   # what may come before an END that begins its line
_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
_LOW_HALF = re.compile(r"\\+u[dD][c-fC-F][0-9a-fA-F]{2}")   # the second half of a pair of escapes (`\udd11`)
_HIGH_HALF = re.compile(r"\\+u[dD][89abAB][0-9a-fA-F]{2}$")


def _tail_run(text: str, ls: int, le: int) -> int:
    """Where the base64 run that ends a line (spaces after it aside) begins, moved back over the backslashes before
    it: a run that begins inside an escape (`\\t`, `\\u2192`, at any depth of JSON held in JSON) is masked with the
    whole escape, and an escaped pair's second half with its first, so no mask leaves half an escape (the partner's
    review: moving past an escape of one level split those of JSON in JSON)."""
    seg = text[ls:le].rstrip(" \t")
    k = ls + len(seg.rstrip(_B64))
    while k > 0 and text[k - 1] == "\\":
        k -= 1
    if _LOW_HALF.match(text, k):
        h = _HIGH_HALF.search(text, max(0, k - 24), k)
        if h:
            k = h.start()
    return k


def _key_tail(text: str, end: int, lo: int = 0, reach: int = 4096) -> int | None:
    """Where the body of a key begins that runs up to `end`, the start of an END, or None. The END begins its line
    (after a line's mark, spaces, a diff's sign), or follows a run of 16 or more on it. The lines before it are
    walked back while each is a base64 run alone after a line's mark, of any length (a key's last line, a PGP
    checksum); the line that stops the walk adds its trailing run when that is 16 or more. A body holds 16
    characters or more. Linear: a pattern of nested runs took exponential time on a long run (the partner's review
    of rc3). The walk reads 4 KB first, and the whole reach only when the END's line or the body runs past it; it
    stops at `lo`, the previous END's end, so ENDs crowded on one long line cost the text once."""
    a = max(lo, end - reach)
    cut = a > lo   # the window stops short of the previous END or the text's start: its first line may run on
    p = end
    while p > a and text[p - 1] in " \t+-":
        p -= 1
    if p == lo or (p > a and text[p - 1] not in _B64 and text[p - 1] not in "\r\n\t\u2192:>|\u2502"):
        return None   # a quote, a bracket, a parenthesis before the END: a mention, not a key's end
    starts, ends = [a], []
    for b in PEM_BREAK.finditer(text, a, end):
        ends.append(b.start())
        starts.append(b.end())
    ends.append(end)
    if starts[-1] == a and cut and reach < PEM_REACH:
        return _key_tail(text, end, lo, PEM_REACH)
    ls, le = starts[-1], end   # the END's own line
    start, size = None, 0
    if not PEM_END_LEAD.fullmatch(text, ls, le):
        k = _tail_run(text, ls, le)
        size = len(text[k:le].rstrip(" \t"))
        if size < 16:
            return None
        start = k
        if not PEM_BODY_LINE.fullmatch(text, ls, le) or (ls == a and cut):
            return start   # other text before the run on the END's line: the body begins at the run
    for i in range(len(ends) - 2, -1, -1):
        ls, le = starts[i], ends[i]
        if ls == a and cut:
            if reach < PEM_REACH:
                return _key_tail(text, end, lo, PEM_REACH)
        elif m := PEM_BODY_LINE.fullmatch(text, ls, le):
            start, size = ls, size + len(m[1])
            continue
        k = _tail_run(text, ls, le)   # the line that stops the walk
        run = len(text[k:le].rstrip(" \t"))
        if run >= 16:
            start, size = k, size + run
        break
    return start if size >= 16 else None


def pem_spans(text: str, jt: JsonText | None = None) -> list[tuple[int, int, bool]]:
    """Every private key in the text, found by its own structure rather than by the scan, so that a key is masked
    whole however its lines are stored, and a placeholder header costs only itself:

    - a key whose header and end lie in one string (JSON) or within 16 KB (other text): header to end;
    - in JSON, a header with no end in its string: the rest of that string, then each following string that is a
      line of the key (an edit's `structuredPatch` stores a file a line per string), up to the end line;
    - in other text, a header with no end: its line, then each following line that is a line of the key.

    Every header goes in one pass, so gitleaks' private-key pattern, which runs from a header to the next
    `KEY-----`, has nothing left to start from: no chain of placeholder headers needs a scan round each. Each span
    says whether it starts a key, so a key is counted once however many strings or lines it spans."""
    spans: list[tuple[int, int, bool]] = []
    for m in PEM_HEADER.finditer(text):
        a = m.start()
        if jt is not None:
            qo, qc, _, ls, le = jt.string_at(a)
            if qo is not None:
                e = PEM_END.search(text, m.end(), qc)
                if e:
                    spans.append((jt.floor(qo, qc, a), jt.ceil(qo, qc, e.end()), True))
                else:
                    spans.append((jt.floor(qo, qc, a), qc, True))
                    spans += [(x, y, False) for x, y in _pem_strings(jt, qc, ls, le)]
                continue
        e = PEM_END.search(text, m.end(), m.end() + PEM_REACH)
        if e and (jt is None or "\n" not in text[a:e.end()]):
            spans.append((a, e.end(), True))
            continue
        z = text.find("\n", m.end())
        z = len(text) if z < 0 else z
        spans.append((a, z, True))
        if jt is not None:
            continue  # a line of a JSON file that is not JSON: within its line
        for _ in range(PEM_FOLLOW):
            if z >= len(text):
                break
            nz = text.find("\n", z + 1)
            nz = len(text) if nz < 0 else nz
            line = text[z + 1:nz]
            if PEM_END.search(line):
                spans.append((z + 1, nz, False))
                break
            kind = _key_line(line)
            if kind is None:
                break
            if kind == "line":
                spans.append((z + 1, nz, False))
            z = nz
    # Every END, the key's body before it: a key printed in two parts has no header here. A whole key's span from its
    # header holds this one, and redact counts the merged mask once; a second key's tail after a whole one is masked
    # too (skipping an END with a header within reach had left it).
    lo = 0
    for m in PEM_END.finditer(text):
        start, lo = _key_tail(text, m.start(), lo), m.end()
        if start is None:
            continue
        if jt is not None:   # in JSON, within the END's string
            qo, qc, _, _, _ = jt.string_at(m.start())
            if qo is not None:
                start = jt.floor(qo, qc, max(start, qo + 1))
        spans.append((start, m.end(), True))
    return spans


def _json_lines_intact(before: str, after: str) -> None:
    """Masking never changes how many lines a JSON text has, nor breaks a line that parsed: it raises instead. A line
    at a time, so a large text is not split whole."""
    if before == after:
        return
    if before.count("\n") != after.count("\n"):
        raise RuntimeError(f"redact: masking changed a JSON text's line count ({before.count(chr(10)) + 1} to "
                           f"{after.count(chr(10)) + 1})")
    i = j = 0
    n = 0
    while True:
        bi, aj = before.find("\n", i), after.find("\n", j)
        o, w = before[i:bi if bi >= 0 else len(before)], after[j:aj if aj >= 0 else len(after)]
        n += 1
        if o != w and MASK in w:
            try:
                json.loads(o)
            except ValueError:
                pass  # it did not parse before either (a pretty-printed document's line, say)
            else:
                try:
                    json.loads(w)
                except ValueError as e:
                    raise RuntimeError(f"redact: masking broke JSON line {n} ({e})") from e
        if bi < 0:
            return
        i, j = bi + 1, aj + 1


def gitleaks_refind():
    """A test of whether gitleaks finds a finding's secret, by its rule, in a text (a temporary file of its own); None
    when gitleaks is not installed."""
    try:
        gitleaks_path()
    except RuntimeError:
        return None

    def refind(text: str, f: dict) -> bool:
        with tempfile.TemporaryDirectory(prefix="errata-refind-") as d:
            p = Path(d) / "piece.jsonl"
            p.write_text(text, encoding="utf-8", errors="surrogateescape")
            got = find([p], Path(d) / "work", 1)[str(p.resolve())]
        return any(g["rule"] == f["rule"] and literal_of(g) == literal_of(f) for g in got)
    return refind


def _fewest(text: str, lines: list[tuple[int, int]], cands: list[tuple[int, int]], f: dict, refind):
    """Of the strings the last resort would mask whole in JSON, the fewest whose masking makes the scan find the secret
    no more, the scan run again on the named lines alone (halving: 37 runs for 6,162 strings of one DataClaw record,
    29 s; masked whole, they had cost 1,366 of its 3,942 messages, version 2026-10-04). None, so that every candidate
    is masked as before, when the scan cannot run, does not find the secret on those lines as written, or still finds
    it with every candidate masked or with the fewest masked."""
    if refind is None or not cands:
        return None
    a0, z0 = min(a for a, _ in lines), max(z for _, z in lines)
    piece = text[a0:z0]

    def found(spans) -> bool:
        pieces, last = [], 0
        for a, z in sorted(spans):
            pieces += [piece[last:a - a0], MASK]
            last = z - a0
        pieces.append(piece[last:])
        return refind("".join(pieces), f)

    if not found([]) or found(cands):
        return None

    def go(c: list, fixed: list) -> list:
        if len(c) <= 1:
            return list(c)
        a, b = c[:len(c) // 2], c[len(c) // 2:]
        if not found(fixed + a):
            return go(a, fixed)
        if not found(fixed + b):
            return go(b, fixed)
        left = go(a, fixed + b)
        return left + go(b, fixed + left)

    got = go(list(cands), [])
    return got if got and not found(got) else None


def _key(f: dict) -> tuple:
    """Findings of one secret placed once: gitleaks reports every occurrence of a token, and each placement finds them
    all (2,000 repeats of one token made 4 million spans)."""
    secret = literal_of(f)
    return secret, bool(f.get("decoded")), (f.get("match") or "") if len(secret) < 6 else ""


def redact(text: str, findings: list[dict], *, pii: bool = True, json_like: bool = False,
           refind="gitleaks") -> tuple[str, dict]:
    """The text with every private key (pem_spans) and every finding masked, and, with `pii`, every email and
    home-path user name; the counts by kind. A finding that cannot be placed, or a secret still present afterwards,
    raises: nothing the scan found is ever published. In JSON text (`json_like`) every mask stays inside strings
    (json_spans), and a mask that would still break a line raises. `refind` is the test of whether the scan still
    finds a secret in a text that narrows the last resort's strings: gitleaks by default, None for none."""
    original = text
    secrets: Counter = Counter()
    jt = JsonText(text) if json_like else None
    spans = [(a, z, "private-key", first) for a, z, first in pem_spans(text, jt)]
    unique: dict[tuple, dict] = {}
    named: dict[tuple, list[tuple[int, int]]] = {}   # every line range the scan named for a secret
    for f in findings:
        unique.setdefault(_key(f), f)
        if f.get("line"):
            named.setdefault(_key(f), []).append((int(f["line"]), int(f.get("end_line") or f["line"])))
    for k, f in unique.items():
        got = place(text, f)
        rule, whole = f["rule"], False
        if not got and named.get(k):
            # Last: the lines the scan named, whole, for a secret it decoded through layers placing does not undo (a
            # value percent-encoded twice stopped the full build of 2026-10-04 before `%XX` read any depth). Counted
            # apart, by its rule, once; in JSON, each string value on those lines as long as the secret or longer, its
            # content whole: what an encoding holds is never longer than the encoding, so the value holding the secret
            # is among them, and a record's short values (`"type": "user"`) stay readable.
            got = [span for a, b in named[k] for span in _line_spans(text, a, b)]
            rule, whole = f"{f['rule']} (whole line)", True
            if jt is not None:
                size = len(literal_of(f))
                lines = got
                got = [(qo + 1, qc) for ls, le in lines for qo, qc, is_key in jt.tokens(ls, le)
                       if not is_key and qc - qo - 1 >= max(size, 1)]
                # A JSON-lines record can be a whole session (a DataClaw export): the strings that hold the secret,
                # as the scan itself tells them, rather than every long string of the record.
                fewer = _fewest(text, lines, got, f, gitleaks_refind() if refind == "gitleaks" else refind)
                if fewer is not None:
                    got, rule = fewer, f"{f['rule']} (the strings that hold it)"
        if not got:
            raise RuntimeError(f"redact: a {f['rule']} finding (line {f.get('line')}) cannot be placed in the text")
        if jt is not None and not whole:
            got = [s for a, z in got for s in json_spans(jt, a, z)]
            if not got:
                raise RuntimeError(f"redact: a {f['rule']} finding (line {f.get('line')}) has nothing to mask "
                                   f"inside a JSON string")
        spans += [(a, z, rule, not whole or i == 0) for i, (a, z) in enumerate(got)]
    if spans:
        merged: list[list] = []
        for a, z, rule, counted in sorted(spans):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], z)
                if counted and not merged[-1][3]:
                    merged[-1][2:] = [rule, True]
            else:
                merged.append([a, z, rule, counted])
        pieces, last = [], 0
        for a, z, rule, counted in merged:
            pieces += [text[last:a], MASK]
            last = z
            secrets[rule] += counted
        pieces.append(text[last:])
        text = "".join(pieces)
        for f in unique.values():
            secret = literal_of(f)
            if len(secret) >= 6 and not f.get("decoded") and secret in text and not (json_like and _crosses(secret)):
                raise RuntimeError(f"redact: a {f['rule']} secret is still present after masking")
            if (json_like and f["rule"] == "private-key" and "\n" not in secret and _crosses(secret)
                    and PEM_END.search(secret)):
                # A key over several strings of one record, from its header to its END: every base64 run in it must be
                # gone. One left means its lines were not recognised; the build stops rather than publish them.
                left = [r for r in re.findall(r"[A-Za-z0-9+/]{16,}", secret) if r in text]
                if left:
                    raise RuntimeError(f"redact: a private key's body survives masking (line {f.get('line')}, "
                                       f"{len(left)} runs)")
    text, runs = mask_key_runs(text)   # after the findings, each placed first: what the scan's rules missed
    if runs:
        secrets["key-run"] += runs
    if not pii:
        if json_like:
            _json_lines_intact(original, text)
        return text, {"secrets": dict(secrets), "emails": 0, "home_users": 0}
    emails = 0

    def mail(m: re.Match) -> str:
        nonlocal emails
        if _keep_email(m[1], m[2]):
            return m[0]
        emails += 1
        return MASK

    text = EMAIL.sub(mail, text)
    homes = 0
    names: set[str] = set()

    def home(m: re.Match) -> str:
        nonlocal homes
        name = m[2].strip()
        if name.lower() in GENERIC_USERS or name == MASK:
            return m[0]
        homes += 1
        names.add(name)
        return m[1] + MASK

    text = HOME_POSIX.sub(home, text)
    text = (HOME_WINDOWS_JSON if json_like else HOME_WINDOWS).sub(home, text)
    for name in sorted(names, key=len, reverse=True):  # whole names, in Claude Code's dashed form
        for form in {name, name.replace(".", "-"), name.replace("_", "-"), name.replace(" ", "-")}:
            for prefix in ("-Users-", "-home-"):
                n = text.count(prefix + form + "-") + text.count(prefix + form + "/")
                if n:
                    text = text.replace(prefix + form + "-", prefix + MASK + "-").replace(prefix + form + "/",
                                                                                          prefix + MASK + "/")
                    homes += n
    text = HOME_DASHED.sub(home, text)
    if json_like:
        _json_lines_intact(original, text)
    return text, {"secrets": dict(secrets), "emails": emails, "home_users": homes}


def redact_lines(src: Path, dst: Path, findings: list[dict]) -> dict:
    """redact() a line at a time, for a large JSON-lines file, secrets only (no `pii`): memory is one line's, not
    a multiple of the file's (a whole 50 MB file held as text took 16 times its size). A record holds a key's lines
    whole, so private keys are found as in redact(). Each finding is placed in the lines it names and in every line
    holding its secret; one placed nowhere raises."""
    unique: dict[tuple, dict] = {}
    lines_of: dict[tuple, set[int]] = {}
    alone_on: dict[tuple, set[int]] = {}   # the lines a match of the secret lies on alone (start and end on it)
    for f in findings:
        k = _key(f)
        unique.setdefault(k, f)
        first, last = int(f.get("line") or 0), int(f.get("end_line") or f.get("line") or 0)
        lines_of.setdefault(k, set()).update(range(first, last + 1))
        if first == last:
            alone_on.setdefault(k, set()).add(first)
    probes = [(k, f, literal_of(f) if len(literal_of(f)) >= 6 and not f.get("decoded") else None)
              for k, f in unique.items()]
    placed: set[tuple] = set()
    secrets: Counter = Counter()
    tmp = Path(str(dst) + ".part")
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        for n, raw in enumerate(fi, 1):
            line = raw.decode("utf-8", errors="surrogateescape")
            body, nl = (line[:-1], "\n") if line.endswith("\n") else (line, "")
            # A finding is placed in each line of its range that holds it (a match can run over several lines, its
            # secret on one of them), and in every other line holding its secret; it must be placed somewhere.
            here, whole = [], []
            for k, f, lit in probes:
                if n in lines_of[k] or (lit and lit in body):
                    if place(body, f):
                        here.append(f)
                    elif n in alone_on.get(k, ()):   # a match on this line alone, placed by no rule: this line,
                        whole.append(({**f, "line": 1, "end_line": 1}, k))   # whole (redact's last resort; a match over lines keeps raising)
            if not here and not whole and not PEM_HEADER.search(body) and not KEY_RUN.search(body):
                fo.write(raw)
                continue
            out, counts = redact(body, here + [f for f, _ in whole], pii=False, json_like=True)
            placed.update([_key(f) for f in here] + [k for _, k in whole])
            secrets.update(counts["secrets"])
            fo.write((out + nl).encode("utf-8", errors="surrogateescape"))
    missing = [f for k, f in unique.items() if k not in placed]
    if missing:
        tmp.unlink()
        raise RuntimeError(f"redact: {len(missing)} findings ({missing[0]['rule']}, line {missing[0].get('line')}) "
                           f"cannot be placed in {Path(src).name}")
    tmp.replace(dst)
    return {"secrets": dict(secrets), "emails": 0, "home_users": 0}


def jsonl(path: Path) -> bool:
    """Whether a JSON text is JSON lines (its first line a JSON value on its own), so it can be redacted a line at a
    time; a pretty-printed document cannot, since a key's lines can lie on several of its lines."""
    with open(path, "rb") as fh:
        first = fh.readline()
    try:
        json.loads(first)
        return True
    except ValueError:
        return False


def _crosses(secret: str) -> bool:
    """A match that runs over a raw line break or an unescaped quote: built across JSON's structure, so only its
    part inside the first string is masked (and a key's other lines by pem_spans), and the rest of it stays."""
    if "\n" in secret:
        return True
    i = secret.find('"')
    while i >= 0:
        n = 0
        while i - n - 1 >= 0 and secret[i - n - 1] == "\\":
            n += 1
        if n % 2 == 0:
            return True
        i = secret.find('"', i + 1)
    return False


def json_like(name: str) -> bool:
    """Whether a file's text is JSON or JSON lines, by its name: Entire's `full.jsonl` and its parts
    (`full.jsonl.001`), `metadata.json`, a copied dataset's `.jsonl`, the build's own `.view.jsonl` and staged
    `<digest>.jsonl.txt` files."""
    n = name.lower()
    return n.endswith((".jsonl", ".json")) or ".jsonl." in n or ".json." in n


def redact_file(path: str, findings: list[dict], *, pii: bool = True) -> tuple[str, dict]:
    """A UTF-8 file's text with its findings masked: (content, counts). The build stages files itself
    (build._stage); this is for the scanner checks."""
    return redact(Path(path).read_bytes().decode("utf-8"), findings, pii=pii, json_like=json_like(Path(path).name))

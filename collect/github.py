"""GitHub's REST and GraphQL APIs with one token, paced under its limits for every source at once.

All sources share one token, so they share these limits (docs.github.com, REST
API rate limits and search):

- code search: 10 requests a minute;
- other searches (commits, issues): 30 a minute;
- everything else: 5,000 an hour;
- GraphQL: 5,000 points an hour;
- and the secondary limits, which refuse bursts of concurrent requests: a few
  requests in flight at once, never more.

Each kind of call takes a token from its own bucket first, and a 403 or 429
that names a rate limit is waited out (`Retry-After`, or the reset time), not
retried at once. A 404 raises `NotFound`; any other refusal raises at once,
since it will not change on the next try.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class NotFound(GitHubError):
    pass


class Bucket:
    """At most `count` calls in any window of `per_s` seconds, shared by every thread."""

    def __init__(self, count: int, per_s: float):
        self.count, self.per_s = count, per_s
        self._times: list[float] = []
        self._lock = threading.Lock()

    def take(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._times = [t for t in self._times if now - t < self.per_s]
                if len(self._times) < self.count:
                    self._times.append(now)
                    return
                wait = self.per_s - (now - self._times[0]) + 0.05
            time.sleep(max(wait, 0.05))


@dataclass
class Limits:
    code_search: Bucket
    search: Bucket
    core: Bucket
    graphql: Bucket

    @classmethod
    def github(cls) -> "Limits":
        # A little under each published limit, so a clock skew or another
        # client on the same token does not push a run over it.
        return cls(code_search=Bucket(9, 60), search=Bucket(27, 60), core=Bucket(4500, 3600),
                   graphql=Bucket(1500, 3600))


class GitHub:
    def __init__(self, token: str | None, *, inflight: int = 4, limits: Limits | None = None,
                 transport: httpx.BaseTransport | None = None, base: str = API):
        if not token:
            raise GitHubError("no GitHub token: set GITHUB_TOKEN (searching GitHub needs one)")
        self.base = base
        self.limits = limits or Limits.github()
        self._inflight = threading.Semaphore(inflight)
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "errata-data-collector"},
            timeout=httpx.Timeout(60.0), transport=transport, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def _send(self, method: str, url: str, bucket: Bucket, *, retries: int = 8, **kw) -> httpx.Response:
        for attempt in range(retries):
            bucket.take()
            with self._inflight:
                try:
                    r = self._client.request(method, url, **kw)
                except httpx.TransportError as e:
                    if attempt == retries - 1:
                        raise GitHubError(f"{method} {url}: {type(e).__name__}") from e
                    time.sleep(min(2 ** attempt, 60))
                    continue
            if r.status_code < 300:
                return r
            if r.status_code == 404:
                raise NotFound(url)
            text = r.text[:300].lower()
            limited = r.status_code == 429 or (r.status_code == 403 and ("rate limit" in text or "abuse" in text))
            if limited:
                wait = _wait_for(r, attempt)
                time.sleep(wait)
                continue
            if r.status_code >= 500 and attempt < retries - 1:
                time.sleep(min(2 ** attempt * 5, 120))
                continue
            raise GitHubError(f"{method} {url}: HTTP {r.status_code}: {r.text[:200]}")
        raise GitHubError(f"{method} {url}: gave up after {retries} tries")

    def get(self, path: str, params: dict | None = None, *, bucket: str = "core") -> dict:
        r = self._send("GET", f"{self.base}/{path.lstrip('/')}", getattr(self.limits, bucket), params=params or {})
        return r.json()

    def search(self, kind: str, q: str, *, page: int = 1, per_page: int = 100, **params) -> dict:
        """One page of a search: kind is "code", "commits", "issues" or "repositories"."""
        bucket = "code_search" if kind == "code" else "search"
        return self.get(f"search/{kind}", {"q": q, "page": page, "per_page": per_page, **params}, bucket=bucket)

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        r = self._send("POST", f"{self.base}/graphql", self.limits.graphql,
                       json={"query": query, "variables": variables or {}})
        body = r.json()
        if body.get("errors") and not body.get("data"):
            raise GitHubError(f"graphql: {str(body['errors'])[:300]}")
        return body.get("data") or {}

    def repo_meta(self, repos: list[str], batch: int = 40) -> dict[str, dict | None]:
        """Each repository's licence, language and fork status, many to a GraphQL query; None for one that is gone.

        The licence is GitHub's own identification: its SPDX id, "none" when the
        repository has no licence file, "unrecognized" when GitHub cannot tell
        which licence a file is (its "NOASSERTION").
        """
        out: dict[str, dict | None] = {}
        todo = sorted(set(repos))
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            parts = []
            for n, full in enumerate(chunk):
                owner, _, name = full.partition("/")
                parts.append(f'r{n}: repository(owner: {_gq(owner)}, name: {_gq(name)}) {{ nameWithOwner url isFork '
                             f'isPrivate createdAt licenseInfo {{ spdxId }} primaryLanguage {{ name }} }}')
            try:
                data = self.graphql("query { " + " ".join(parts) + " }")
            except GitHubError:
                data = {}
                for n, full in enumerate(chunk):  # one bad name must not cost the batch
                    try:
                        data[f"r{n}"] = self.graphql("query { " + parts[n] + " }").get(f"r{n}")
                    except GitHubError:
                        data[f"r{n}"] = None
            for n, full in enumerate(chunk):
                node = data.get(f"r{n}")
                out[full] = None if not node else {
                    "repo": node["nameWithOwner"], "repo_url": node["url"], "fork": node["isFork"],
                    "private": node["isPrivate"], "created_at": node["createdAt"],
                    "repo_license": licence_label(node.get("licenseInfo")),
                    "repo_language": (node.get("primaryLanguage") or {}).get("name"),
                }
        return out


def licence_label(info: dict | None) -> str:
    if not info or not info.get("spdxId"):
        return "none"
    spdx = info["spdxId"]
    return "unrecognized" if spdx == "NOASSERTION" else spdx


def _gq(value: str) -> str:
    """A GraphQL string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _wait_for(r: httpx.Response, attempt: int) -> float:
    """How long a rate-limited response asks to wait: Retry-After, else until the reset, else a growing pause."""
    if r.headers.get("retry-after", "").isdigit():
        return float(r.headers["retry-after"]) + 1
    if r.headers.get("x-ratelimit-remaining") == "0" and r.headers.get("x-ratelimit-reset", "").isdigit():
        return max(float(r.headers["x-ratelimit-reset"]) - time.time(), 0) + 2
    return min(60 * (attempt + 1), 600)

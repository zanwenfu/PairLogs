"""Observable signals about each repository owner, for aggregate use only (by design, 2026-10-26: no
model-inferred trait of any person): account type, account creation date, public repository count, and for an
organisation whether its own website or email is a university's (.edu, .ac.<cc>, .edu.<cc>). From GitHub's GraphQL
through gh's own login. Kept local, never released per owner. argv: owners list (one a line), output JSON."""
import json
import re
import subprocess
import sys

UNIVERSITY = re.compile(r"(\.edu(\.[a-z]{2})?|\.ac\.[a-z]{2})(/|$|>|\s)", re.I)
owners = [l.strip() for l in open(sys.argv[1]) if l.strip()]
q = lambda s: json.dumps(s)
out = {}
for i in range(0, len(owners), 40):
    chunk = owners[i:i + 40]
    parts = [f'r{n}: repositoryOwner(login: {q(o)}) {{ __typename ... on User {{ createdAt repositories(privacy: PUBLIC) {{ totalCount }} }} '
             f'... on Organization {{ createdAt websiteUrl email repositories(privacy: PUBLIC) {{ totalCount }} }} }}'
             for n, o in enumerate(chunk)]
    for attempt in range(4):   # a failed call would mark the whole chunk gone: retry, then stop
        r = subprocess.run(["gh", "api", "graphql", "-f", "query=query { " + " ".join(parts) + " }"],
                           capture_output=True, text=True)
        data = json.loads(r.stdout).get("data") if r.stdout.strip().startswith("{") else None
        if data:   # an owner GitHub no longer has comes back as null beside an error: that one is "gone"
            break
    else:
        sys.exit(f"GitHub did not answer for owners {i}-{i + len(chunk)}: {r.stderr[:200]}")
    for n, o in enumerate(chunk):
        node = data.get(f"r{n}")
        if not node:
            out[o] = {"type": "gone"}
            continue
        rec = {"type": node["__typename"], "created": node.get("createdAt"),
               "public_repos": (node.get("repositories") or {}).get("totalCount")}
        if node["__typename"] == "Organization":
            site = " ".join(x for x in (node.get("websiteUrl"), node.get("email")) if x)
            rec["university"] = bool(UNIVERSITY.search(site + " "))
        out[o] = rec
json.dump(out, open(sys.argv[2], "w"))
import collections
print(len(out), dict(collections.Counter(v["type"] for v in out.values())),
      "university orgs", sum(1 for v in out.values() if v.get("university")))

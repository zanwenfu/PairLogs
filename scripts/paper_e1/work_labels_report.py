"""The work-label pilot's tally: calls, cost, unreadable answers, the label shares (repositories, sessions, by group)
and the full run's cost at the pilot's own tokens per call. argv: items JSONL, pilot JSONL."""
import collections
import json
import sys

items = {i["session_key"]: i for i in map(json.loads, open(sys.argv[1]))}
rows = [json.loads(l) for l in open(sys.argv[2])]
by_kind = collections.defaultdict(list)
for r in rows:
    by_kind[r["id"].split(":", 1)[0]].append(r)
print(f"calls {len(rows)}: " + ", ".join(f"{k} {len(v)}" for k, v in by_kind.items()))
print(f"HTTP errors {sum(r['status'] != 200 for r in rows)}, unreadable {sum(r['status'] == 200 and not r['answer'] for r in rows)}")
usd = sum(r["usd"] for r in rows)
print(f"cost ${usd:.4f}; per call: " + ", ".join(
    f"{k} ${sum(r['usd'] for r in v) / len(v):.5f} ({sum(r['in'] for r in v) / len(v):.0f} in, {sum(r['out'] for r in v) / len(v):.0f} out)"
    for k, v in by_kind.items()))


def share(counter, n):
    return ", ".join(f"{k} {c} ({100 * c / n:.0f}%)" for k, c in counter.most_common())


def label(r, field):
    a = r.get("answer") or {}
    return ((a.get(field) or {}).get("label") or "unreadable").strip().lower()


repo_rows = by_kind.get("repo", [])
print(f"\nKIND OF PROJECT, {len(repo_rows)} repositories: " + share(collections.Counter(label(r, "project") for r in repo_rows), len(repo_rows)))
print(f"DOMAIN, {len(repo_rows)} repositories: " + share(collections.Counter(label(r, "domain") for r in repo_rows), len(repo_rows)))
proj_rows = by_kind.get("project", [])
if proj_rows:
    print(f"KIND OF PROJECT, {len(proj_rows)} sessions with no repository: " + share(collections.Counter(label(r, "project") for r in proj_rows), len(proj_rows)))
sess = by_kind.get("session", [])
print(f"\nKIND OF TASK, {len(sess)} sessions: " + share(collections.Counter(label(r, "task") for r in sess), len(sess)))
groups = collections.defaultdict(collections.Counter)
for r in sess:
    for g in items[r["id"].split(":", 1)[1]]["groups"]:
        groups[g.split(":")[0]][label(r, "task")] += 1
for g, c in groups.items():
    print(f"  {g} ({sum(c.values())}): " + share(c, sum(c.values())))
# project kind per session, through its repository
repo_label = {r["id"].split(":", 1)[1]: label(r, "project") for r in repo_rows}
sp = collections.Counter()
for k, i in items.items():
    sp[repo_label.get(i.get("repo") or "", None) or next((label(r, "project") for r in proj_rows if r["id"] == "project:" + k), "repository gone")] += 1
print(f"\nKIND OF PROJECT per session ({len(items)}): " + share(sp, len(items)))
also = sum(1 for r in rows if any(((r.get("answer") or {}).get(f) or {}).get("also") for f in ("project", "domain", "task")))
print(f"answers with an 'also': {also} of {len(rows)}")
# the full run, at the pilot's own mean cost per call: final version about 47,000 sessions with typed text,
# about 3,500 repositories, about 5,500 sessions with no repository
m = {k: sum(r["usd"] for r in v) / len(v) for k, v in by_kind.items()}
est = 47000 * m.get("session", 0) + 3500 * m.get("repo", 0) + 5500 * m.get("project", m.get("repo", 0))
print(f"\nfull run at these means: about ${est:.0f} (47,000 sessions, 3,500 repositories, 5,500 sessions without one)")

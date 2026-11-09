"""Each repository's description, topics, main languages and README (first 4,000 characters), from GitHub's
GraphQL through gh's own login; a repository gone gives an empty record. argv: items JSONL (the pilot's) or a .txt
list of owner/name (work_labels_items.py), output JSONL."""
import json
import subprocess
import sys
import time

if sys.argv[1].endswith(".txt"):
    repos = sorted({l.strip() for l in open(sys.argv[1]) if "/" in l})
else:
    items = [json.loads(l) for l in open(sys.argv[1])]
    repos = sorted({i["repo"] for i in items if i.get("repo") and "/" in i["repo"]})
q = lambda s: json.dumps(s)
README = ["README.md", "readme.md", "README", "README.rst", "README.txt", "Readme.md"]
out = open(sys.argv[2], "w")
gone = 0
for i in range(0, len(repos), 15):
    chunk = repos[i:i + 15]
    parts = []
    for n, r in enumerate(chunk):
        o, name = r.split("/", 1)
        files = " ".join(f'f{k}: object(expression: {q("HEAD:" + f)}) {{ ... on Blob {{ text }} }}' for k, f in enumerate(README))
        parts.append(f'r{n}: repository(owner: {q(o)}, name: {q(name)}) {{ nameWithOwner description '
                     f'repositoryTopics(first: 10) {{ nodes {{ topic {{ name }} }} }} '
                     f'languages(first: 4, orderBy: {{field: SIZE, direction: DESC}}) {{ nodes {{ name }} }} {files} }}')
    for attempt in range(4):   # a failed call is asked again, so a whole chunk is not marked gone by one error
        r = subprocess.run(["gh", "api", "graphql", "-f", "query=query { " + " ".join(parts) + " }"], capture_output=True, text=True)
        try:
            data = json.loads(r.stdout or "{}").get("data") or {}
        except ValueError:
            data = {}
        if data:
            break
        time.sleep(10 * (attempt + 1))
    else:
        sys.exit(f"chunk {i}: no data after 4 tries: {r.stderr[:200]}")
    for n, repo in enumerate(chunk):
        node = data.get(f"r{n}")
        if not node:
            gone += 1
            out.write(json.dumps({"repo": repo, "gone": True}) + "\n")
            continue
        readme = next((node[f"f{k}"]["text"] for k in range(len(README)) if node.get(f"f{k}") and node[f"f{k}"].get("text")), None)
        out.write(json.dumps({"repo": repo, "current": node["nameWithOwner"], "description": node.get("description"),
                              "topics": [t["topic"]["name"] for t in node["repositoryTopics"]["nodes"]],
                              "languages": [l["name"] for l in node["languages"]["nodes"]],
                              "readme": (readme or "")[:4000] or None}, ensure_ascii=False) + "\n")
out.close()
print(len(repos), "repositories;", gone, "gone")

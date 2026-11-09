"""Counts only: Entire sessions in the fixed version with metadata.json and initial_attribution, kept vs copies, tier
A vs B; per row how many metadata.json; value types and a summary of agent_percentage."""
import json, collections, glob, statistics
import pyarrow.parquet as pq
W = "/path/to/pairlogs-work"
V = f"{W}/run2/build-fix2/2026-11-02"
R = f"{W}/release-fix2/2026-11-02/a-names"
tierA = set()
for f in glob.glob(f"{R}/data/entire/*.parquet"):
    tierA.update(pq.read_table(f, columns=["session_key"])["session_key"].to_pylist())
c = collections.Counter(); per_row = collections.Counter(); types = collections.Counter(); pcts = []
paths = collections.Counter()
for f in sorted(glob.glob(f"{V}/data/entire/*.parquet")):
    t = pq.read_table(f, columns=["session_key", "duplicate_of", "files", "n_user_messages"])
    for k, dup, fl, nu in zip(*[t[x].to_pylist() for x in ("session_key", "duplicate_of", "files", "n_user_messages")]):
        metas = [x for x in fl or [] if x["path"].endswith("metadata.json")]
        per_row[len(metas)] += 1
        for x in metas: paths[x["path"].count("/")] += 1
        att = None
        for x in metas:
            try:
                d = json.loads(x["content"] or "")
            except ValueError:
                continue
            if isinstance(d, dict) and isinstance(d.get("initial_attribution"), dict):
                att = d["initial_attribution"]
        kept = dup is None
        tier = "A" if k in tierA else "B"
        c[(tier, "kept" if kept else "copy", "attr" if att else "no attr")] += 1
        if att and kept:
            for kk, v in att.items(): types[(kk, type(v).__name__)] += 1
            if isinstance(att.get("agent_percentage"), (int, float)): pcts.append(att["agent_percentage"])
            if (nu or 0) > 0: c[("kept with attr and a typed message",)] += 1
print(sorted(c.items()))
print("metadata.json per row:", sorted(per_row.items()), "| path depth:", sorted(paths.items())[:6])
print("types:", sorted(types.items()))
if pcts:
    q = statistics.quantiles(pcts, n=10)
    print("agent_percentage kept: n", len(pcts), "min", min(pcts), "deciles", [round(x, 1) for x in q], "max", max(pcts),
          "| >=99:", sum(p >= 99 for p in pcts), "| ==0:", sum(p == 0 for p in pcts))

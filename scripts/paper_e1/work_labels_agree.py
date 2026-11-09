"""Read-only: how often two runs of the work labels give the same main label, per field (kind of project, domain, kind
of task), on the items both answered; e.g. the batch run without the form against the pilot's check run with it.
Counts and label pairs only, no text.

python work_labels_agree.py A.jsonl B.jsonl
"""
import collections
import json
import sys


def labels(path: str) -> dict[tuple[str, str], str]:
    out = {}
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        for field, v in (r.get("answer") or {}).items():
            if isinstance(v, dict) and v.get("label"):
                out[(r["id"], field)] = v["label"]
    return out


def main() -> None:
    a, b = labels(sys.argv[1]), labels(sys.argv[2])
    both = sorted(set(a) & set(b))
    by = collections.defaultdict(lambda: [0, 0])
    pairs = collections.Counter()
    for k in both:
        by[k[1]][0] += 1
        by[k[1]][1] += a[k] == b[k]
        if a[k] != b[k]:
            pairs[(k[1], a[k], b[k])] += 1
    for field, (n, same) in sorted(by.items()):
        print(f"{field}: same main label {same} of {n} ({same / n:.0%})")
    print(f"all: {sum(s for _, s in by.values())} of {len(both)}; only in A {len(set(a) - set(b))}, only in B {len(set(b) - set(a))}")
    for (field, x, y), n in pairs.most_common(12):
        print(f"   {field}: {x} -> {y}: {n}")


if __name__ == "__main__":
    main()

"""Numbers in two JSON results side by side, list items matched by their label (every text field), not position.
Prints every number that differs (with the change), and items one side lacks; a count of those equal.
python kdiff.py OLD.json NEW.json"""
import json
import sys


def label(d):
    """Every text field of a list item: an item can be named by two (account and agent)."""
    if isinstance(d, dict):
        parts = [f"{k}={v}" for k, v in d.items() if isinstance(v, (str, bool)) or v is None]
        return ",".join(parts) or None
    return None


def leaves(d, p=""):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from leaves(v, f"{p}.{k}" if p else str(k))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            yield from leaves(v, f"{p}[{label(v) or i}]")
    elif isinstance(d, (int, float)) and not isinstance(d, bool):
        yield p, d



A, B = sys.argv[1], sys.argv[2]
a = dict(leaves(json.load(open(A))))
b = dict(leaves(json.load(open(B))))
same = moved = 0
print(f"== {A} -> {B}: {len(a)} and {len(b)} numbers; {len(set(a) & set(b))} matched")
for k in dict.fromkeys(list(a) + list(b)):
    x, y = a.get(k), b.get(k)
    if x is None or y is None:
        print(f"  only in {'new' if x is None else 'old'}: {k[:120]} {x if x is not None else y}")
        continue
    if x != y:
        rel = f"{100 * (y - x) / abs(x):+.2f}%" if x else "n/a"
        print(f"  {k[:120]:120s} {x!s:>12} {y!s:>12} {rel:>9}")
        moved += 1
    else:
        same += 1
print(f"  equal: {same}; differ: {moved}")

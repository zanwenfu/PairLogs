"""The second scanner's findings (second_scan.py's private hits file) sorted by what each distinct value looks like, from
what the hits file keeps (detector, the public format marker, length, shape, whether a connection string's password
is a placeholder), never the value:

  provider format       a provider's own key format (ghp_, glpat-, npm_, sk-, hf_, ...; a private key; an AWS key)
  connection string     a URI with a user and a password that is not a placeholder
  placeholder           a URI whose password is a placeholder (password, changeme, ${VAR}, ...)
  near a service name   a generic string (a UUID, a run of letters and digits) that the detector took for a key only
                        because a service's name stands near it

Prints and writes (OUT.json) counts per detector and class. With --want-class CLASS[,CLASS] --want W --files F
[--sample N --seed S], writes the sha256s of those classes' values (or N per detector, drawn with the seed) and the
published files they were found in, for second_scan.py --files/--want/--values-out.

python sort_hits.py HITS.jsonl OUT.json [--want-class ... --want W.txt --files F.txt [--sample N] [--seed S]]
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

PREFIXES = {"ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_", "glpat-", "gldt-", "glrt-", "glptt-", "glsoat-",
            "npm_", "sk-", "sk-ant-", "sk-proj-", "sk-or-", "hf_", "gsk_", "xoxb-", "xoxp-", "xoxa-", "xoxe-",
            "xapp-", "sk_live_", "rk_live_", "pk_live_", "sk_test_", "rk_test_", "r8_", "fw_", "dckr_pat_", "pypi-",
            "shpat_", "shpss_", "lin_api_"}
STRUCTURED = {"PrivateKey", "AWS", "AWSSessionKey", "JWT", "GCP", "GCPApplicationDefaultCredentials", "SlackWebhook",
              "DiscordWebhook", "AzureBatch"}   # the detector's own pattern is the provider's format
URIS = {"URI", "Postgres", "MongoDB", "Redis", "JDBC", "SQLServer", "FTP", "LDAP", "AMQP", "RabbitMQ", "MySQL"}
CLASSES = ("provider format", "connection string", "placeholder", "near a service name")


def kind(h: dict) -> str:
    if h.get("placeholder_password") is True:
        return "placeholder"
    if h["detector"] in URIS or h.get("placeholder_password") is False:
        return "connection string"
    if h["detector"] in STRUCTURED or (h.get("prefix") or "") in PREFIXES:
        return "provider format"
    return "near a service name"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("hits", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--want-class")
    ap.add_argument("--want", type=Path)
    ap.add_argument("--files", type=Path)
    ap.add_argument("--sample", type=int)
    ap.add_argument("--seed", default="sort-hits-20261105")
    a = ap.parse_args()
    values: dict[tuple, dict] = {}
    where: dict[tuple, set] = defaultdict(set)
    finds = Counter()
    for line in open(a.hits, encoding="utf-8"):
        h = json.loads(line)
        k = (h["detector"], h["sha256"])
        values.setdefault(k, h)
        where[k].add(h["published_file"])
        finds[k] += 1
    table: dict[str, Counter] = defaultdict(Counter)
    for k, h in values.items():
        table[h["detector"]][kind(h)] += 1
    summary = {"findings": sum(finds.values()), "distinct values": len({s for _, s in values}),
               "by class (distinct values)": dict(Counter(kind(h) for h in values.values())),
               "by detector and class (distinct values)": {d: dict(c) for d, c in
                                                           sorted(table.items(), key=lambda x: -sum(x[1].values()))}}
    a.out.write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "by detector and class (distinct values)"}))
    for d, c in list(summary["by detector and class (distinct values)"].items())[:40]:
        print(f"  {d}: {c}")
    if a.want_class:
        if not (a.want and a.files):
            ap.error("--want-class needs --want and --files")
        classes = set(a.want_class.split(","))
        if not classes <= set(CLASSES):
            ap.error(f"classes are {CLASSES}")
        chosen = [k for k, h in sorted(values.items()) if kind(h) in classes]
        if a.sample:
            rnd = random.Random(a.seed)
            per = defaultdict(list)
            for k in chosen:
                per[k[0]].append(k)
            chosen = [k for d in sorted(per) for k in rnd.sample(per[d], min(a.sample, len(per[d])))]
        a.want.write_text("".join(sorted({s + "\n" for _, s in chosen})))
        a.files.write_text("".join(sorted({f + "\n" for k in chosen for f in where[k] if f != "?"})))
        print(f"wanted: {len({s for _, s in chosen})} values in {len({f for k in chosen for f in where[k]})} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""A Croissant 1.0 description (machine-readable metadata, JSON-LD) of a release's tier A: its tables (sessions,
messages, helpers' messages, Entire's line attribution) as file sets, each column a field with its type and description read from docs/columns/*.md, so the two cannot drift apart.

python scripts/croissant.py TIER_A_DIR OUT.json --version 2026-11-02 [--url URL] [--creator NAME]
"""
import argparse
import json
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs" / "columns"
TABLES = {"sessions": ("data", "sessions.md", "One row per session (per file for Aider and SpecStory)."),
          "conversations": ("conversations", "conversations.md", "One row per piece of a session's conversation."),
          "conversations_subagents": ("conversations_subagents", "conversations_subagents.md",
                                      "One row per piece of a helper agent's conversation inside a session."),
          "attribution": ("attribution", "attribution.md",
                          "One row per Entire session with Entire's own count of the committed lines its agent wrote.",
                          "attribution/entire.parquet")}
TYPES = {"string": "sc:Text", "text": "sc:Text", "int": "sc:Integer", "bool": "sc:Boolean", "timestamp": "sc:Date",
         "float": "sc:Float", "double": "sc:Float"}
CONTEXT = {"@language": "en", "@vocab": "https://schema.org/", "cr": "http://mlcommons.org/croissant/",
           "dct": "http://purl.org/dc/terms/", "sc": "https://schema.org/", "citeAs": "cr:citeAs",
           "column": "cr:column", "conformsTo": "dct:conformsTo", "data": {"@id": "cr:data", "@type": "@json"},
           "dataType": {"@id": "cr:dataType", "@type": "@vocab"}, "examples": {"@id": "cr:examples", "@type": "@json"},
           "extract": "cr:extract", "field": "cr:field", "fileObject": "cr:fileObject", "fileProperty": "cr:fileProperty",
           "fileSet": "cr:fileSet", "format": "cr:format", "includes": "cr:includes", "isLiveDataset": "cr:isLiveDataset",
           "jsonPath": "cr:jsonPath", "key": "cr:key", "md5": "cr:md5", "parentField": "cr:parentField", "path": "cr:path",
           "recordSet": "cr:recordSet", "references": "cr:references", "regex": "cr:regex", "repeated": "cr:repeated",
           "replace": "cr:replace", "separator": "cr:separator", "source": "cr:source", "subField": "cr:subField",
           "transform": "cr:transform"}


def columns(md: str) -> list[tuple[str, str, str]]:
    """(name, type, description) for each row of a column document's table."""
    out = []
    for line in (DOCS / md).read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|", line)
        if m:
            out.append(m.groups())
    return out


def field(table: str, name: str, typ: str, desc: str) -> dict:
    base = typ.lower().removeprefix("list of ").rstrip("s")
    repeated = typ.lower().startswith("list of")
    data_type = TYPES.get(base, "sc:Text")   # a record or a list of records: its JSON text
    f = {"@type": "cr:Field", "@id": f"{table}/{name}", "name": name, "description": re.sub(r"`", "", desc),
         "dataType": data_type, "source": {"fileSet": {"@id": table}, "extract": {"column": name}}}
    if repeated and base in TYPES:
        f["repeated"] = True
    return f


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tier_a")
    ap.add_argument("out")
    ap.add_argument("--version", required=True)
    ap.add_argument("--url", default="")
    ap.add_argument("--creator", default="")
    a = ap.parse_args()
    root = Path(a.tier_a)
    dist, sets = [], []
    for table, (folder, md, desc, *inc) in TABLES.items():
        if not (root / folder).is_dir():
            continue
        dist.append({"@type": "cr:FileSet", "@id": table, "name": table, "encodingFormat": "application/x-parquet",
                     "includes": inc[0] if inc else f"{folder}/*/*.parquet"})
        sets.append({"@type": "cr:RecordSet", "@id": f"{table}-records", "name": table, "description": desc,
                     "field": [field(table, *c) for c in columns(md)]})
    doc = {"@context": CONTEXT, "@type": "sc:Dataset", "conformsTo": "http://mlcommons.org/croissant/1.0",
           "name": "PairLogs", "version": a.version,
           "description": ("Real developer sessions with AI coding agents from six public sources, read into one schema, "
                           "de-duplicated across sources, masked for secrets and for the names people type, with each message "
                           "marked by who wrote it. Tier A: the full text of sessions whose repository carries a "
                           "recognised open licence, and of the DataClaw and OpenHands feedback datasets."),
           "license": ("Each session keeps its source's licence: repo_license on each session row; DataClaw and "
                       "OpenHands feedback MIT. The code is MIT."),
           "isLiveDataset": False, "distribution": dist, "recordSet": sets}
    if a.url:
        doc["url"] = a.url
    if a.creator:
        doc["creator"] = {"@type": "sc:Person", "name": a.creator}
    Path(a.out).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{a.out}: {len(sets)} record sets, {sum(len(s['field']) for s in sets)} fields")


if __name__ == "__main__":
    main()

"""The dataset card (README.md), written from the version as built: every number is recounted from the Parquet files
and the sources table, never carried over from the run's own tallies."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from collect import formats
from collect.config import HF_REPO

SCRAPED_ABOUT = {
    "entire": ("Entire", "Sessions recorded with the Entire CLI (github.com/entireio/cli) in public GitHub repositories. "
               "Found by GitHub's commit search for the `Entire-Checkpoint:` trailer; read anonymously with git from "
               "where Entire keeps checkpoints (the `entire/checkpoints/v1` branch, `refs/entire/checkpoints/*`, or a "
               "checkpoint repository the settings name). Each session's latest copy: its `metadata.json`, the agent's "
               "own transcript (`full.jsonl`, and `full.jsonl.NNN` parts past 50 MB), and its subagents' files. Entire "
               "redacts secrets itself before storing them; its PII redaction is off by default."),
    "specstory": ("SpecStory", "Chat histories SpecStory saves under `.specstory/history/` and its users commit to "
                  "public GitHub repositories. Found by GitHub code search; every file of the folder is read from the "
                  "default branch, including those over code search's 384 KB limit. One row per file."),
    "aider": ("Aider", "Aider chat histories (`.aider.chat.history.md`) committed to public GitHub repositories. Found by "
              "GitHub code search; one row per file, kept whole (a file holds every session started in that folder)."),
}
COUNT_DEFINITIONS = [
    ("n_user_entries", "every entry the format records in the user's role: for Claude Code this includes tool "
                       "results, which it records as user entries; for SpecStory, its User blocks; for Aider, its "
                       "blocks of `####` lines (which can include a level-4 heading in a reply)."),
    ("n_user_messages", "the messages a person typed, counted from the conversations rows (`typed_by_developer`, a "
                        "queued message delivered later counted once); empty where who typed cannot be told (Codex "
                        "sessions with no typed-message mark; a scripted run such as `codex exec`). For a format "
                        "that marks no author it is a rule's count, not a recorded one; each format's rule is under "
                        "Who typed what, by agent."),
    ("n_agent_messages", "the agent's messages with visible text (for Claude Code, distinct API messages, its "
                         "`<synthetic>` replies excluded)."),
    ("n_tool_calls", "the agent's tool calls; 0 for Aider, whose edits are SEARCH/REPLACE blocks. SpecStory's "
                     "older files name no tool: their calls count, with no `tool_name` or `category`."),
    ("has_tool_results", "whether the files record a tool's output; empty where the session's files were not read."),
    ("has_timestamps", "whether the files record times; `started_at` and `ended_at` are the earliest and latest, in "
                       "UTC: the session's first and last message's where its messages carry times, else as the "
                       "source records them, and empty where no time with a zone is recorded (a time without one is "
                       "kept as written in `source_facts`). A session time the source records that differs from its "
                       "messages' (a file's own time, a feedback's) is kept in `source_facts` (`recorded_started_at`, "
                       "`recorded_ended_at`). A duplicate has no rows and keeps its source's times: one DataClaw "
                       "record's end is before its start, as DataClaw stores it (its kept copy's are in order)."),
]
KEY_DEFINITIONS = [
    ("session_key", "`<source>:<source_id>`, the row's key, unique across the collection."),
    ("session_id", "the agent's own session id where the source records one (Entire's `metadata.json`), else empty."),
    ("checkpoint_pk", "for Entire and SWE-chat v2 (whose sessions come from Entire's checkpoints), "
                      "`<repository>#<checkpoint id>` of the checkpoint the files were read from, as SWE-chat writes "
                      "it; empty elsewhere."),
    ("strategy", "for Entire and SWE-chat v2, the CLI's checkpoint strategy as its `metadata.json` records it; empty "
                 "elsewhere."),
    ("n_conversation_rows", "the session's rows in `conversations`; empty for a duplicate and where the session's "
                            "files were not read (no reader for its agent, or no file to read), 0 where they were "
                            "read and gave no rows (no message in them, or a file the reader could not take); for "
                            "either, `source_facts` (`conversations_error`) says why."),
    ("n_subagent_rows", "the session's rows in `conversations_subagents`."),
]
# Who typed a user message, per format: the agent's own marks, measured on the collection's sessions
# (design decisions of the collector's repository, 2026-10).
TYPED_RULES = [
    ("Claude Code", "the errata-bench study's rules, from Claude Code's own marks (`origin`, `turnOrigin`, "
                    "`promptSource`, `isMeta`, queue operations): slash commands count, `/clear` included; not "
                    "typed: compaction summaries, plans accepted, teammates' and other agents' messages, Claude "
                    "Code's notices, prompts the agent scheduled for itself (`ScheduleWakeup`, `CronCreate`, "
                    "`/loop`), interruptions and `!` shell input (the developer's actions, not messages). A message "
                    "queued while the agent worked is typed where it was queued; its delivery is a copy "
                    "(`copy_of`). Words given with a refused tool call are typed, on the tool result's row. A session "
                    "run through the SDK or `claude -p` (its transcript's `entrypoint` `sdk-cli`, `sdk-py` or "
                    "`sdk-ts`) with a single message, queued or not, is a program's call: who wrote it is unknown "
                    "(empty). One with more messages stays typed, as apps built on the SDK are interactive too; the "
                    "session table does not show the entry point."),
    ("Codex", "`user_message` events (older versions) or `UserMessage` items (newer); a model-facing message is typed "
              "when it carries one's text. Sessions with neither: unknown."),
    ("Gemini CLI", "`user` messages; in its `{role, parts}` files, user messages holding `<USER_REQUEST>`."),
    ("Copilot CLI", "`user.message` events less a subagent's (`source` `agent-...`) and automation (`source` "
                    "`autopilot` or `skill-...`, a `<skill-context>` expansion); `parentAgentTaskId` is telemetry, "
                    "not authorship."),
    ("Pi, Oh My Pi", "user messages less skill expansions (`<skill>`); extensions' messages are not typed."),
    ("OpenCode", "user text parts not marked synthetic."),
    ("Cursor", "user messages holding `<user_query>`, less Cursor's own fixed text inside it: its instructions to "
               "the agent (\"Briefly inform the user about the task\", \"Perform any necessary follow-up actions\", "
               "notes on a subagent's result), an accepted plan (the developer's `plan`) and its interface's buttons. "
               "A query sent again unchanged, and a scheduled prompt sent the same way in many sessions, stay typed: "
               "nothing marks them."),
    ("SpecStory, Aider, DataClaw, Factory Droid, Devin, Kiro, Roger Roger, Project Runner",
     "nothing in the format marks text the tool inserts (Devin marks every user line as input): a user message is "
     "taken as the developer's unless its text shows a harness's, an IDE's or a tool's (a wrapper's or a notice's "
     "opening, a command's template). A rule's reading, not a mark the format recorded; Project Runner's delegated "
     "prompts are the agent's. Aider writes its own yes/no questions, with the developer's one-key answer, as input "
     "lines (`Add file to the chat? (Y)es/(N)o ... [Yes]: y`, 250 rows): they read as typed. Aider's benchmark "
     "script's prompts, which end in its fixed instructions, are the script's, not typed. So are Claude Code's own "
     "requests to the model (a next prompt's suggestion, a summary's request, a Stop hook's feedback, the /loop "
     "skill's body, a subagent's warm-up), a scheduler's firings (`[cron:<id>]`) and an orchestrator's templates "
     "(megaplan's, by their openings and its name or their `Project directory:` line), and commands' and tools' "
     "bodies sent as the message (OpenCode's /review, a few commands' bodies, review, distilling and orchestration "
     "tools' templates: each by its opening and, where a person might write the same, a second sentence of it). A "
     "template no rule names stays typed."),
]
OUT_OF_SCOPE = [
    ("Amp threads", "most need a sign-in since Amp ended public sharing on 2026-06-02 (0 of 15 sampled threads linked "
                    "from commits were readable)"),
    ("OpenCode share links", "their Terms of Use forbid crawling and copying shared content; OpenCode sessions that "
                             "Entire records are in the Entire subset"),
    ("Pi share links", "a few hundred, in secret gists; deferred"),
    ("Cursor exports", "text only, no model names; deferred"),
    ("Copilot coding agent logs", "not viewable signed out"),
    ("AIDev", "agent-written pull requests, not sessions"),
    ("DevGPT", "ChatGPT chats from 2023, not coding-agent sessions"),
    ("Hugging Face datasets tagged agent-traces", "real and synthetic sessions mixed, most with no licence"),
    ("TraceLab", "no message text"),
    ("Claude Code on the web, Codex cloud, Devin", "viewing needs a login"),
    ("Cursor and Windsurf shares", "team-only"),
    ("Codex, Gemini and Copilot CLIs, Zed, Continue without a recorder", "sessions stay on the developer's machine"),
    ("Cline, Roo and Kilo exports, git-note tools", "almost nothing public"),
]


# Where versions are kept, and how to get one: a university cluster's shared storage for the first versions
# (2026-10-06). The Hugging Face front matter (viewer configs, access form) is written only for a Hugging Face upload,
# when ERRATA_CARD_HF=1. The Globus collection is the one the upload of 2026-10-04 went to: its paths start at
# the storage's root, so a full path on the cluster drops that root there (the full path gave "Directory Not Found").
_CLUSTER_EXAMPLE = {"name": "a shared cluster", "host": "login.example.edu", "root": "/data/errata-data",
                    "globus": "an institution's Globus collection", "globus_root": "/errata-data", "owner": "maintainer",
                    "account": "cluster account"}


def cluster() -> dict:
    """Where a deployment keeps its versions, from ERRATA_CLUSTER (a JSON object with the keys of the example); the
    example's neutral values otherwise. The first deployment's values live in its own environment, not in the code."""
    raw = os.environ.get("ERRATA_CLUSTER")
    return {**_CLUSTER_EXAMPLE, **(json.loads(raw) if raw else {})}

# The card's front half, for a reader who wants to use the data: one line per source, the columns most analyses touch,
# and the queries most start from. The reference sections below keep the full definitions.
SOURCE_LINES = {
    "entire": ("Entire", "logs the Entire CLI saves while a developer works with an agent, committed to public GitHub "
                         "repositories: the agent's own complete transcript."),
    "specstory": ("SpecStory", "chat histories the SpecStory editor extension saves as Markdown, committed to public "
                               "GitHub repositories. One row per file."),
    "aider": ("Aider", "Aider's chat-history files committed to public GitHub repositories. One row per file; a file "
                       "can hold several sessions."),
    "swe-chat-v2": ("SWE-chat v2", "SALT-NLP's public dataset of coding sessions, copied as released."),
    "dataclaw": ("DataClaw", "session exports that developers uploaded to Hugging Face with the DataClaw tool, copied "
                             "as released."),
    "openhands-feedback": ("OpenHands feedback", "OpenHands' public dataset of user sessions with their feedback, "
                                                 "copied as released."),
}
CONVERSATION_COLUMNS = [
    ("`session_key`, `turn_number`", "The session, and the row's position in it. Together they identify a row."),
    ("`kind`", "What the row is: `text`, `thinking`, `tool_call`, `tool_result`, `notice`, `command`, `shell`, "
               "`queued`, `compaction`, `interrupt`, `rejection`, `plan`, `progress`, `snapshot`."),
    ("`actor`", "Who made it: `developer`, `agent`, `tool`, `harness` (the agent program's own text, such as its "
                "notices) or `peer` (another agent, or a teammate)."),
    ("`typed_by_developer`", "`true` if a person typed this message; empty where the format cannot tell. See Before "
                             "you rely on a number."),
    ("`copy_of`", "A message typed while the agent was busy is queued, then delivered. The delivered row points to "
                  "the queued one: skip rows with `copy_of` set to count each message once."),
    ("`content`", "The text, with secrets as `[REDACTED]`. A tool's output over 100,000 characters is cut, as "
                  "SWE-chat cuts them (`content_truncated`); the whole of it is in the session's files."),
    ("`tool_name`, `command`, `file_path`, `pattern`, `tool_input_json`", "What the agent ran, and on what."),
    ("`category`", "A tool call's kind, by SWE-chat's rules: `Action` (changes something), `Research` (reads), "
                   "`Orchestration` (starts or manages other agents) or `Other`."),
    ("`bash_category`", "A shell command's kind: `file ops`, `git`, `package manager`, `test/build` or `other`. A "
                        "tool's output takes its call's."),
    ("`is_error`", "The tool's output was marked as failed."),
    ("`model`, `timestamp`, `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`",
     "The model and its token counts, recorded once per model call: on its first text row, else its first tool "
     "call."),
    ("`is_conversational`", "`true` for the developer's prompts and the agent's replies: the dialogue without the "
                            "tools and notices."),
]
SESSION_COLUMNS = [
    ("`session_key`", "`<source>:<id>`, unique across the dataset; it links to `conversations`."),
    ("`source`, `agent`, `agent_version`, `models`", "Where the session came from, the agent program and the models "
                                                    "it used. An agent the files do not name is empty here, `unknown` "
                                                    "in the tables above."),
    ("`repo`, `repo_url`, `repo_license`, `repo_language`", "The GitHub repository, its licence (`none`: no licence "
                                                            "file; `unrecognized`: GitHub cannot tell) and main "
                                                            "language. Empty for DataClaw and OpenHands, which record "
                                                            "no repository."),
    ("`started_at`, `ended_at`", "The first and the last message's time, in UTC."),
    ("`n_user_messages`", "Messages the developer typed, each counted once; empty where who typed cannot be told."),
    ("`n_agent_messages`, `n_tool_calls`", "The agent's replies with text, and its tool calls."),
    ("`n_user_entries`", "Every entry in the user's role, typed or not (for Claude Code this includes tool outputs)."),
    ("`n_conversation_rows`, `n_subagent_rows`", "The session's rows in the two conversation tables."),
    ("`duplicate_of`, `also_in`", "For a session found in several places: the copy kept, and every place it was "
                                  "found. A duplicate has no rows."),
    ("`files`", "The session's raw files as published: `path`, `bytes`, `sha256`, `content`."),
    ("`redactions`", "How many secrets, emails and home-folder names were masked in it."),
    ("`source_facts`", "JSON with what only some sources have: Entire's checkpoint ids, a copied dataset's name and "
                       "revision, a fingerprint of the typed messages."),
    ("`found_url`, `found_commit`, `fetched_at`, `collector_version`, `content_sha256`, `dedup_key`, `file_format`, "
     "`bytes`, `has_tool_results`, `has_timestamps`, `session_id`, `checkpoint_pk`, `strategy`",
     "Where and how the session was fetched, and what its files record. Defined in full in the reference below."),
]
RECIPES = [
    ("What developers asked", "`conversations` where `typed_by_developer` and `copy_of` is null"),
    ("Pushback moments", "`pushback = true`; the rows just before show what the agent had done"),
    ("What the agent did", "`kind = 'tool_call'`, then `tool_name`, `category`, `command`, `file_path`"),
    ("Failed tool calls", "`is_error = true`"),
    ("One agent's sessions", "`agent = 'Claude Code'` (or `'Codex'`, `'Cursor'`, ...)"),
    ("The dialogue only", "`is_conversational = true`"),
    ("One row per distinct session", "a session table where `duplicate_of` is null"),
    ("The raw transcripts", "`files` in the session tables"),
]


def _labels(version_dir: Path, source: str):
    path = version_dir / "data" / source
    if not path.exists():
        return None
    cols = ["source_id", "agent", "repo", "repo_license", "started_at", "duplicate_of", "bytes", "has_tool_results",
            "n_user_messages", "redactions", "source_facts"]
    return ds.dataset(str(path), format="parquet").to_table(columns=cols).to_pylist()


def _near_duplicates(version_dir: Path) -> tuple[int, int]:
    """Kept sessions repeating another kept session's typed messages in the same repository; and the kept sessions
    that have a fingerprint (two or more typed messages)."""
    groups: Counter = Counter()
    for source in ("entire", "specstory", "aider", "swe-chat-v2", "dataclaw", "openhands-feedback"):
        for r in _labels(version_dir, source) or []:
            if r["duplicate_of"]:
                continue
            fp = json.loads(r["source_facts"] or "{}").get("typed_fingerprint")
            if fp:
                groups[(r["repo"], fp)] += 1
    return sum(n - 1 for n in groups.values()), sum(groups.values())


def _pushback_line(version_dir: Path, changelog: dict) -> list[str]:
    """The `pushback` column's description and its counts on typed rows, where any row holds one."""
    got: Counter = Counter()
    for path in sorted((version_dir / "conversations").rglob("*.parquet")):
        if "pushback" not in pq.read_schema(path).names:
            return []
        t = pq.read_table(path, columns=["pushback"], filters=[("typed_by_developer", "=", True)])
        got.update(t.column("pushback").to_pylist())
    if not got[True] and not got[False]:
        return []
    meta = changelog.get("pushback_labels") or {}
    high_only = (meta.get("content_filter") or {}).get("high_only")   # the filter set to block only high severity
    why = {"content_filter": "refused by Azure's content filter"
           + (", even set to block only high-severity content" if high_only else ""),
           "cyber_policy": "refused under the service's "
           "cybersecurity policy, asked three more times", "refused (model)": "the model declined to answer, asked "
           "three more times", "empty": "empty"}
    unread = sorted((meta.get("rows_unread") or {}).items(), key=lambda kv: -kv[1])
    reasons = "; ".join(f"{n:,} {why.get(k, k)}" for k, n in unread) if sum(n for _, n in unread) == got[None] else ""
    return ([f"`pushback` is `true` when a typed message pushes back on the agent's preceding work: it corrects or "
            "redirects it, rejects or reverts it, or reports it broken. Each message was read on its own by "
            f"{meta.get('model') or 'a model'}, with no session context and no reasoning"
            + (f" ({meta['date']})" if meta.get("date") else "")
            + ", one call per distinct text; the prompt is in the changelog (`pushback_labels`). It is a model's "
            "reading, not checked against human labels.", "",
            f"- Typed rows: {got[True]:,} pushback, {got[False]:,} not, {got[None]:,} unread"
            + (f" ({reasons})" if reasons and got[None] else "") + ". Every other row is empty."]
           + ([f"- A text over {cut['over']:,} characters was sent as its first {cut['head']:,} and last "
               f"{cut['tail']:,} characters ({cut['distinct']:,} texts, {cut['rows']:,} rows)."]
              if (cut := meta.get("cut")) else [])
           + ([f"- Of {cp['rows']:,} typed rows that copy an earlier typed message of their session (`copy_of`), "
               f"{cp['text_differs']:,} differ from it in text and were read on their own; {cp['label_differs']:,} of "
               "those got a different label."] if (cp := meta.get("copies")) else [])
           + ["- SWE-chat's own `prompt_pushback` (four classes, from a model that read the whole session) stays "
              "beside it, on its sessions' rows."])


def _redacted_alone(version_dir: Path) -> Counter:
    """Per source, the typed prompts whose whole text is a mask."""
    out: Counter = Counter()
    for path in sorted((version_dir / "conversations").rglob("*.parquet")):
        t = pq.read_table(path, columns=["source", "content"],
                          filters=[("typed_by_developer", "=", True), ("char_count", "<=", 16)])
        for source, text in zip(t.column("source").to_pylist(), t.column("content").to_pylist()):
            if (text or "").strip() == "[REDACTED]":
                out[source] += 1
    return out


def _aider_benchmark(version_dir: Path) -> tuple[int, int, int]:
    """Aider's sessions run by its benchmark script; their prompts, the user rows its script wrote
    (formats.aider_benchmark); and the blocks of its test output still read as the agent's: in such a session, a reply
    opening with a bare fence right after a notice (a notice came between it and the script's next prompt)."""
    root = version_dir / "conversations" / "aider"
    sessions, prompts, left = set(), 0, 0
    for path in sorted(root.rglob("*.parquet")) if root.exists() else []:
        by: dict = {}
        for r in pq.read_table(path, columns=["session_key", "turn_number", "turn_type", "actor", "content"]).to_pylist():
            by.setdefault(r["session_key"], []).append(r)
        for key, rows in by.items():
            rows.sort(key=lambda r: r["turn_number"])
            mine = sum(1 for r in rows if r["turn_type"] == "user_prompt" and r["actor"] == "harness"
                       and formats.aider_benchmark(r["content"] or ""))
            if not mine:
                continue
            sessions.add(key)
            prompts += mine
            left += sum(1 for a, b in zip(rows, rows[1:]) if a["turn_type"] == "system_event"
                        and b["turn_type"] == "assistant_response" and (b["content"] or "").split("\n", 1)[0].strip() == "```")
    return len(sessions), prompts, left


def write(version_dir: Path, changelog: dict, log=print) -> None:
    strict = changelog.get("redaction") == "strict"   # else most secrets, not every one (decision 78)
    lines, configs = [], []
    counts = {}
    for source in ("entire", "specstory", "aider", "swe-chat-v2", "dataclaw", "openhands-feedback"):
        rows = _labels(version_dir, source)
        if rows is None:
            continue
        configs.append((source, f"data/{source}/*.parquet"))
        kept = [r for r in rows if not r["duplicate_of"]]
        starts = sorted(r["started_at"] for r in rows if r["started_at"])
        secrets = sum((r["redactions"] or {}).get("secrets") or 0 for r in rows)   # a count since rc15
        counts[source] = {
            "rows": len(rows), "kept": len(kept), "duplicates": len(rows) - len(kept),
            "repos": len({r["repo"] for r in rows if r["repo"]}), "agents": Counter(r["agent"] for r in rows),
            "top_repos": Counter(r["repo"] for r in rows if r["repo"]).most_common(3),
            "licences": Counter(r["repo_license"] or "not looked up" for r in rows), "bytes": sum(r["bytes"] or 0 for r in kept),
            "first": starts[0] if starts else None, "last": starts[-1] if starts else None,
            "secrets": secrets, "emails": sum((r["redactions"] or {}).get("emails") or 0 for r in rows),
            "home_users": sum((r["redactions"] or {}).get("home_users") or 0 for r in rows),
            # SWE-chat's own reading of a repository's licence, where it differs from GitHub's (repo_license)
            "licence_readings_differ": sum(1 for r in kept if (sw := json.loads(r["source_facts"] or "{}")
                                                               .get("swechat_license_type")) and sw != r["repo_license"]),
        }
    conv = {}
    for table in ("conversations", "conversations_subagents"):
        root = version_dir / table
        if root.exists() and any(root.rglob("*.parquet")):
            configs.append((table, f"{table}/*/*.parquet"))
            for path in root.rglob("*.parquet"):
                conv.setdefault(path.parent.name, Counter())[table] += pq.ParquetFile(path).metadata.num_rows
    copied = changelog.get("copied") or {}
    for name, info in sorted(copied.items()):
        for cfg_name, pattern in info.get("configs", []):
            configs.append((cfg_name, pattern))

    q = json.dumps  # a JSON string is a valid YAML scalar, whatever it holds
    header = ["---", "license: other", "license_name: per-source", "pretty_name: PairLogs",
              "tags:", "- agent-traces", "- coding-agents", "- software-engineering",
              "extra_gated_prompt: " + q("This collection is for research only. Each session keeps the licence of the "
                                         "repository or dataset it came from (the repo_license column and the sources "
                                         "of the copied datasets); some repositories have none. Do not attempt to "
                                         "identify the people in these sessions."),
              "extra_gated_fields:", "  Name: text", "  Affiliation: text", "  Intended use: text",
              "  " + q("I will use this dataset for research only, and respect each source's licence") + ": checkbox",
              "configs:"]
    for name, pattern in configs:
        header += [f"- config_name: {q(name)}", f"  data_files: {json.dumps(pattern if isinstance(pattern, list) else [pattern])}"]
    header.append("---")
    hf = os.environ.get("ERRATA_CARD_HF") == "1"   # only a Hugging Face upload needs the front matter
    if hf:
        lines += header

    repo = os.environ.get("ERRATA_HF_REPO") or HF_REPO
    found = sum(c["rows"] for c in counts.values())
    kept_all = sum(c["kept"] for c in counts.values())
    agents = Counter()
    for c in counts.values():
        agents.update({a: n for a, n in c["agents"].items() if a})
    firsts = [c["first"][:10] for c in counts.values() if c["first"]]
    lasts = [c["last"][:10] for c in counts.values() if c["last"]]
    conv_rows = sum(c["conversations"] for c in conv.values())
    sub_rows = sum(c["conversations_subagents"] for c in conv.values())
    pb = changelog.get("pushback_labels")
    pb_line = _pushback_line(version_dir, changelog)
    span = f" They started between {min(firsts)} and {max(lasts)}." if firsts else ""
    top = [a for a, _ in agents.most_common(5)]
    top_agents = ", ".join(top[:-1]) + f" and {top[-1]}" if len(top) > 1 else "".join(top)
    lines += ([""] if hf else []) + ["# PairLogs", "",
              f"Real sessions between developers and AI coding agents, collected from public sources. Version "
              f"`{changelog['version']}`.", "",
              f"{kept_all:,} sessions ({found:,} found, before duplicates were set aside)"
              + (f" from {len(agents)} coding agents, the most common being {top_agents}" if agents else "")
              + f".{span} Each session is split into rows, one "
              f"per piece of a message: what the developer typed, the agent's replies and reasoning, every tool call and "
              f"what it returned. That gives {conv_rows:,} rows, plus {sub_rows:,} from helper agents the sessions "
              "started. Secrets are masked.", "",
              "Labels are read from each session's own files, or set by fixed rules where a format records nothing (who "
              "typed a message, in some formats). "
              + ("Two come from a model instead: `pushback`, a model's reading of each typed message made for this "
                 "dataset, and SWE-chat's own prompt labels, carried over to its sessions' rows as SWE-chat wrote them. "
                 "Nothing judges quality or whether a session is real." if pb_line else
                 "No label here was written by a model; SWE-chat's own prompt labels, which a model wrote, are carried "
                 "over to its sessions' rows as SWE-chat wrote them. Nothing judges quality, intent or whether a "
                 "session is real."), "",
              ]
    files = [p for p in version_dir.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    CLUSTER = cluster()
    where = f"{CLUSTER['root']}/{changelog['version']}"
    sums = f"PairLogs-{changelog['version']}.sha256"
    lines += ["## Where it is", "",
              f"This version is on {CLUSTER['name']}, at `{where}` ({size / 1e9:,.1f} GB, "
              f"{len(files):,} files), with a checksum list beside it (`{CLUSTER['root']}/{sums}`). It is in temporary "
              "storage: files not read or changed for 180 days are deleted, and nothing there is backed up.", "",
              f"- On a machine of the cluster, read it where it is.",
              f"- To copy all of it, use Globus: the collection "
              f"\"{CLUSTER['globus']}\", path `{CLUSTER['globus_root']}/{changelog['version']}` (the collection's "
              "paths start at the storage's root).",
              # `<netid>`, not `NETID`: the build masks the card for personal data, and `NETID@login.cs.duke.edu`
              # reads as an email (the card of 2026-10-04 showed `[REDACTED]:` before the path, 2026-10-06)
              f"- To copy a part, use rsync through `{CLUSTER['host']}` with your {CLUSTER['account']}, your user name in place "
              "of `<netid>`. For example, the conversations tables:", "",
              "```bash",
              f"rsync -a --info=progress2 <netid>@{CLUSTER['host']}:{where}/conversations "
              f"./PairLogs-{changelog['version']}/",
              "```", "",
              f"To check a full copy, run `sha256sum -c {sums}` in the folder that holds it.", "",
              "## Quick start", "",
              "The files are Parquet, so DuckDB, pandas or polars read them directly. The paths below are relative to "
              f"the folder that holds `{changelog['version']}` (`{CLUSTER['root']}` on the cluster):", ""]
    if pb_line:
        lines += ["```python", "import duckdb", "",
                  "# typed messages, and the share that push back, by agent",
                  "duckdb.sql(\"\"\"",
                  "    SELECT agent, count(*) AS typed_messages, round(avg(pushback::int), 3) AS pushback_share",
                  f"    FROM '{changelog['version']}/conversations/*/*.parquet'",
                  "    WHERE typed_by_developer AND copy_of IS NULL",
                  "    GROUP BY agent ORDER BY typed_messages DESC",
                  "\"\"\").show()", "```", ""]
    lines += ["```python", "import pandas as pd", "",
              # without `columns`, pandas reads `files` too: 11.3 GB for Entire in 2026-10-04, more memory than a
              # Duke login session has (4 GB) (2026-10-06)
              "# one source's sessions, without `files` (the raw transcripts, most of the data's size)",
              f"sessions = pd.read_parquet('{changelog['version']}/data/entire',",
              "                           columns=['session_key', 'agent', 'repo', 'started_at', 'n_user_messages'])",
              "```", ""]
    lines += ["Rows of one session share `session_key`; `turn_number` gives their order.", "",
              "## What's in it", "",
              "| Folder | One row is | Rows |", "|---|---|---|",
              f"| `data/<source>/`, one per source ({', '.join(counts)}) | a session, with its raw files (`files`) | "
              f"{found:,} |",
              f"| `conversations/<source>/` | one piece of a message: text, reasoning, a tool call, a tool's output, a "
              f"notice | {conv_rows:,} |",
              f"| `conversations_subagents/<source>/` | the same, for helper agents a session started | {sub_rows:,} |",
              "| `copied/` | the copied datasets' own files, as their publishers released them | see Sources |", "",
              "`changelog.json` holds this version's counts, its redactions by rule and the records of fixes made after "
              "the build.", "",
              "## Sources", "", "| Source | Sessions | Kept | Repositories | Agents | Started between |",
              "|---|---|---|---|---|---|"]
    for source, c in counts.items():
        named = c["agents"].most_common()
        shown = ", ".join(f"{a or 'unknown'} {n:,}" for a, n in named[:4])   # None: not recorded
        more = f", and {len(named) - 4} more" if len(named) > 4 else ""
        when = f"{c['first'][:10]} to {c['last'][:10]}" if c["first"] else "n/a (no times recorded)"
        lines.append(f"| {source} | {c['rows']:,} | {c['kept']:,} | {c['repos']:,} | {shown}{more} | {when} |")
    lines += [""] + [f"- **{SOURCE_LINES[s][0]}**: {SOURCE_LINES[s][1]}" for s in counts if s in SOURCE_LINES]
    swe = _labels(version_dir, "swe-chat-v2") or []
    if swe:
        dup = sum(1 for r in swe if r["duplicate_of"])
        lines.append(f"- SWE-chat v2 was built from Entire's checkpoints too: {dup:,} of its {len(swe):,} sessions are "
                     f"also in the Entire subset and are kept there, so it adds {len(swe) - dup:,} of its own. Its prompt "
                     "labels still reach those sessions' rows, matched by session id.")
    bench_sessions, bench_prompts, bench_left = _aider_benchmark(version_dir)
    if bench_sessions:
        lines.append(f"- {bench_sessions:,} of Aider's {counts['aider']['rows']:,} sessions are runs of Aider's benchmark "
                     f"script on coding exercises, not a developer's work: their {bench_prompts:,} prompts are the "
                     "script's (`actor` harness, not typed)"
                     + (f"; {bench_left:,} blocks of its test output that a notice follows (Aider's own, or a line of "
                        "the output opening with `> `) are still read as the agent's replies." if bench_left else "."))
    lines += ["", "One repository can hold much of a source (a project's own sessions, or a script's runs); the three "
              "largest of each, duplicates included:", "",
              "| Subset | Largest three repositories: sessions (share of the subset's) |", "|---|---|"]
    for source, c in counts.items():
        top = "; ".join(f"{r} {n:,} ({n / c['rows']:.1%})" for r, n in c["top_repos"]) or "no repository recorded"
        lines.append(f"| {source} | {top} |")
    lines += ["", "## Columns", "", "### `conversations`", "", "| Column | Meaning |", "|---|---|"]
    lines += [f"| {cols} | {meaning} |" for cols, meaning in CONVERSATION_COLUMNS]
    if pb_line:
        lines.append("| `pushback` | Written by a model: `true` if a typed message pushes back on what the agent did "
                     "(corrects or redirects it, rejects or reverts it, or says it is broken). See Labels written by a "
                     "model. |")
    lines += ["| `prompt_intent`, `prompt_pushback`, `language` | SWE-chat's own labels, written by a model, only on "
              "the prompts of sessions SWE-chat v2 holds (in the `swe-chat-v2` and `entire` folders): `user_prompt` "
              "rows, and Claude Code's `system_injected` rows and rows of a queued message (`queue_operation`), "
              "which SWE-chat v2 labels as prompts. |",
              "| `role`, `turn_type` | SWE-chat's scheme for the same rows, kept so the two tables line up. Who wrote "
              "a row is in `actor` and `typed_by_developer`, not here. |",
              "| `turn_id`, `session_id`, `checkpoint_pk`, `repo_id`, `source_entry_uuid`, `response_id`, `parent_uuid`, "
              "`raw_file`, `raw_line` | Ids, and the row's place in the session's original files. |",
              "| `conversation_turn_number`, `content_block_index`, `queue_op_subtype`, `orchestration_kind`, "
              "`is_continuation`, `is_first_turn`, `word_count`, `char_count`, `agent`, `strategy` | As in "
              "SWE-chat's table. |",
              "| `source` | The source the session came from, as in the session tables. |", "",
              "`conversations_subagents` holds subagents' rows (`task_pk`, `turn_pk`, `agent_id` first), as SWE-chat "
              "keeps them apart, in the same columns less `queue_op_subtype`, `copy_of` and `pushback` (a subagent "
              "queues no messages, and nobody types to one). Each row carries its parent session's ids, and "
              "`turn_number` counts within the subagent's own rows, so `turn_id` repeats the parent's values: "
              "`session_key` and `turn_pk` identify a row.", "",
              "### Session tables (one per source)", "", "| Column | Meaning |", "|---|---|"]
    lines += [f"| {cols} | {meaning} |" for cols, meaning in SESSION_COLUMNS]
    lines += ["", "## Common queries", "", "| To get | Use |", "|---|---|"]
    lines += [f"| {want} | {how} |" for want, how in RECIPES if pb_line or "pushback" not in how]
    lines += ["", "## Labels written by a model", ""] + pb_line + [
              "- `prompt_intent` and `prompt_pushback` are SWE-chat's labels, written by a model, on the rows where "
              "one of SWE-chat v2's prompts matches; how they were matched is in the reference below.", "",
              "## Before you rely on a number", "",
              "- **Who typed a message is read where the agent records it, else a rule's guess.** Claude Code, Codex, "
              "Gemini CLI, Copilot CLI, Cursor, OpenCode and Pi mark it. SpecStory, Aider, DataClaw and a few others "
              "do not: there a user message counts as typed unless its text is plainly a tool's (a notice, a "
              "template). A script driving an agent then reads as a person: one Gemini CLI repository holds hundreds "
              "of sessions of short work-item titles. The rules for each agent are in the reference below.",
              "- **Some fields depend on the agent.** An `assistant_thinking` row can be empty: the transcript kept the "
              "thinking's signature, not its text (Claude Code with newer models; Codex keeps its reasoning "
              "encrypted). Tokens are as each agent records them for one model call, on the call's first text row, "
              "else its first tool call (SWE-chat's placement). Anthropic's models count `input_tokens` without cache "
              "reads; OpenAI's (Codex) count cached input inside `input_tokens`. A count recorded below zero "
              "(OpenCode's output, in 6 rows) is left empty.",
              "- **SpecStory shows a tool call and its output as one block**, so its `tool_call` row holds both and no "
              "`tool_result` row follows; the hourly files a session-logging tool for Claude Code writes beside them "
              "record each result apart, as `tool_result` rows.",
              "- **Masking catches most secrets, not every one**; see Privacy."]
    near, kept_typed = _near_duplicates(version_dir)
    if kept_typed:
        lines.append(f"- **Near-copies stay.** {near:,} of the {kept_typed:,} kept sessions with two or more typed "
                     "messages repeat another kept session's typed messages, in order, in the same repository (a "
                     "session copied with a few bytes changed, or an automation sending the same prompts). "
                     "`source_facts.typed_fingerprint` (a digest of the typed messages in order) finds them.")
    lines += ["", "Known gaps, each counted on this version:", ""] + [f"- {g}" for g in changelog.get("gaps") or []]
    lines += ["", "## How it was built", "",
              "1. **Collect.** Entire's logs and SpecStory's and Aider's histories were found through GitHub's commit "
              "and code search and read from public repositories. SWE-chat v2, DataClaw and OpenHands feedback were "
              "copied from Hugging Face.",
              "2. **Split into rows.** A reader for each agent's log format splits every message into pieces, each a "
              "row, in order. Rows are read from the session's own files, never from another dataset's table.",
              "3. **Label.** Who made each row, and whether a person typed it, come from the agent's own marks where "
              "it records them, else from fixed rules. `category` and `bash_category` follow SWE-chat's rules."
              + (" `pushback` was written by a model." if pb_line else ""),
              "4. **Mask.** Every published file and row was scanned for secrets and personal data, and each find "
              "replaced by `[REDACTED]`.",
              "5. **Set duplicates aside.** A session found in several places keeps one copy with rows, the most "
              "complete; the others have `duplicate_of` set and no files, and `also_in` lists every place. Matching "
              "is exact: the agent's own session id, else a digest of the content. One conversation saved twice is one "
              "session too: the same typed messages with mostly the same replies, in one repository or export; or, "
              "where a source records no agent, the same session id with the same whole conversation. Copied "
              "datasets keep every session (only their secrets are masked); "
              "their matches with the other sources are in `copied/matches.parquet`.", ""]
    lines += ["## Privacy", "",
              "Every file was scanned with gitleaks "
              f"(rules `{(changelog.get('scan') or {}).get('rules')}`: the defaults, with exceptions for machine ids "
              "and commit hashes calibrated on real transcripts), as fetched and again as published, its JSON read "
              "decoded too. Each secret found became `[REDACTED]`, wherever the file holds it.",
              "",
              "- Private keys were masked by their structure, whatever the scan found.",
              "- A run after a key's own prefix (Anthropic's `sk-ant-api03-`, OpenAI's `sk-proj-`, GitHub's `ghp_` and "
              "`github_pat_`), which the scan's rules miss where a key is cut short or broken over lines, was masked "
              "whatever its length, up to the break: a key broken over lines can keep its tail after the break, a "
              "fragment that cannot authenticate"
              + (f" ({changelog['key_runs_masked']:,} in the sessions' files of this version; a copied dataset's are in "
                 "its count below)." if changelog.get("key_runs_masked") else "."),
              "- Email addresses (except no-reply addresses, `git@` remotes and example domains) and the user name in "
              "home paths were masked too, except in the copied datasets (SWE-chat, DataClaw, OpenHands' feedback) "
              "and their sessions' conversation rows, which are masked for secrets only: their emails and home paths "
              "are as their publishers released them. As their cards say: SWE-chat redacted personal information in "
              "prompts, replies and subagents' text with Presidio and TruffleHog (not in tool calls and results, "
              "where home paths remain); most DataClaw exports hash user names; OpenHands' users chose to make their "
              "feedback public.",
              "- " + ("Secrets were masked in the conversations' rows and in the session's files." if strict else
                      "The bar is most secrets, not every one: the collection is for analysis, and its sources are "
                      "public already. A secret the scan found but could not place in its text is left there and "
                      "counted, and the published text is checked by scanning a random sample of it again.")
              + (f" After a full check of the published text, {cf['strings']:,} more spans of {cf['distinct']:,} "
                 f"strings it found, and {cf['personal']:,} personal items, were masked in the files the same way, "
                 "and the check was run again." if (cf := changelog.get("check_found_masked")) else "")
              + " The unredacted files are not published.", ""]
    whole = _redacted_alone(version_dir)
    if whole:
        lines += ["Typed prompts that are `[REDACTED]` alone (a message that was a secret, or one its publisher "
                  "redacted whole): " + ", ".join(f"{s} {n:,}" for s, n in whole.most_common()) + ".", ""]
    lines += ["| Subset | Secrets | Emails | Home-path names |", "|---|---|---|---|"]
    for source, c in counts.items():
        lines.append(f"| {source} | {c['secrets']:,} | {c['emails']:,} | {c['home_users']:,} |")
    copied_rows = []
    for name, info in sorted(copied.items()):
        if not info.get("copied"):   # a linked dataset holds no file here
            continue
        r = info.get("redactions") or {}
        copied_rows.append((name, sum((r.get("secrets") or {}).values()), r.get("emails", 0), r.get("home_users", 0)))
    if copied_rows:
        lines.append(f"| copied datasets' files ({len(copied_rows)}) | {sum(x[1] for x in copied_rows):,} | "
                     f"{sum(x[2] for x in copied_rows):,} | {sum(x[3] for x in copied_rows):,} |")
        lines += ["", "The copied sources' own rows show 0: their sessions' text is in their copied files, counted in "
                  "the last row.", ""]
        lines += _details("Masked spans per copied dataset", ["| Dataset | Secrets | Emails | Home-path names |",
                                                              "|---|---|---|---|"]
                          + [f"| {n} (copied files) | {a:,} | {b:,} | {c:,} |" for n, a, b, c in copied_rows])
    lines += ["", "## Licences", "", "| Subset | Licences (rows) |", "|---|---|"]
    for source, c in counts.items():
        lines.append(f"| {source} | " + ", ".join(f"{k} {v:,}" for k, v in c["licences"].most_common(8)) + " |")
    lines += ["", "`repo_license` is GitHub's reading of the repository's licence. Repositories with no licence grant "
              "nothing beyond GitHub's terms, which let public content be viewed and forked on GitHub; they are "
              "included and labelled, so they can be filtered out. Copied datasets keep their own licences, listed "
              "below.", ""]
    lines += _details("Licences of the copied datasets",
                      [f"- **{name}**: {_licence_line(info)}" for name, info in sorted(copied.items())])
    lines += ["## Access and removal", "",
              "Use the data for research only, and respect each source's licence. Do not try to identify the people "
              "in these sessions. Who can read the cluster copy follows the cluster's accounts and the folder's permissions.",
              "",
              "A session or repository is taken out of the next version on request: ask the maintainer (the folder's "
              f"owner, `{CLUSTER['owner']}`), naming it. Sessions deleted from their source are not removed on their own: the "
              "collection keeps what it fetched until asked. A copied dataset is published whole: a session of it "
              "removed on request loses its rows, but its file stays in the copy until the dataset itself is removed "
              "or linked.", "",
              "## Reference", ""]
    lines += _details("Who typed what, by agent",
                      ["- `typed_by_developer`: whether the row's text is a message the developer typed; empty where "
                       "the format cannot tell. A format that records no mark telling a person from a script driving "
                       "the agent cannot tell them apart, and neither can this column.",
                       "- Who typed what, by format:"] + [f"  - {a}: {t}" for a, t in TYPED_RULES] + [
                       "- A row marked typed holds the developer's own words: the wrappers a tool puts around them "
                       "(Cursor's `<user_query>` and its context blocks, Gemini CLI's `<USER_REQUEST>`, OpenClaw's "
                       "metadata blocks before a chat message, a harness's `User steering update:` around "
                       "`<user_input>`, Codex's IDE and in-app browser context before `## My request for Codex:`) "
                       "and the blocks a harness or an IDE adds to the same message (`<ide_opened_file>`, "
                       "`<ide_selection>`, `<system-reminder>`), and the blocks tools and plugins insert (system "
                       "instructions and notifications, an in-app browser's context, Cursor's branch diff, uploaded "
                       "documents and rules, CI-monitor events, shell notices, heartbeats), are taken off wherever they "
                       "are (a developer's own pasted text holding one of these tags would lose it too; none was found "
                       "in version 2026-10-04); Codex's replies to the agent's questions keep the developer's answers; "
                       "the session's files keep the message as written. A message that is a harness's, an IDE's or a "
                       "tool's alone (task notices, compaction summaries, command output, a scheduled task or a "
                       "wake-up, a command template), another agent's (a delegation) or a teammate's is not typed, "
                       "whatever the agent.",
                       "- Aider: its benchmark script sends each coding exercise and each failed test run as input, "
                       "ending in the script's fixed instructions, and writes each test run's output into the history "
                       "unmarked; those prompts are the script's (not typed), and that output, after Aider's notices "
                       "and before the script's next prompt or the session's end, is a notice, not the agent's reply. "
                       "Aider writes a notice of several lines with `> ` on its first line only: a failed edit's (`# 1 "
                       "SEARCH/REPLACE block failed to match!`) is read whole, to its closing request, not as part of "
                       "the agent's reply.",
                       "- Claude Code's user entries are filed as SWE-chat files them (`system_injected`: meta entries, "
                       "local-command output, system reminders, shell output; every other user entry `user_prompt`, "
                       "slash commands included). Every user-role message of the other agents is `user_prompt`, as "
                       "SWE-chat files them."])
    lines += _details("How labels were matched and checked",
                      ["- `prompt_intent` and `prompt_pushback` are SWE-chat's labels, written by a model, carried over "
                       "to the rows where a prompt matches one of SWE-chat v2's, in the same session: by SWE-chat's "
                       "entry id where its text agrees, else by the text (spaces normalized; emails and SWE-chat's "
                       "anonymized tokens alike; Cursor's `<user_query>` and `<timestamp>` wrappers and image "
                       "placeholders left out, and both sides cleaned of the blocks tools insert as a typed row's text "
                       "is), else by its first 200 characters where one prompt on each side alone begins so, each "
                       "also with our masks standing for what SWE-chat's copy shows; then a Claude Code entry filed "
                       "`system_injected` (an entry Claude Code marks as its own, such as a command's expansion or a "
                       "prompt the agent scheduled for itself) by SWE-chat's entry id where its text agrees; last, a "
                       "Codex message holding an image by its text without the image's tags (SWE-chat labels the tags "
                       "and the words apart), and a message queued in Claude Code by its queued row, each only where "
                       "the row it belongs to is plain (a text no prompt or `system_injected` row holds, as many "
                       "rows as labels); "
                       "`language` likewise. Per agent, where SWE-chat's labelled prompts landed (a row marked typed, "
                       "another row, none) and by which rule is in the changelog (`conversations.swechat_labels`). "
                       "SWE-chat labels some text no one typed (command expansions, injected context), which lands on "
                       "no typed row.",
                       "- `category` and `bash_category` follow SWE-chat's own rules, read from its 11.6 million rows: "
                       "a tool's category by its name (SWE-chat's three sets; the same kinds of tool in agents SWE-chat "
                       "does not hold), a shell command's by its first word, a command run through `bash -lc` by the "
                       "command it runs (99.1% agreement when applied to SWE-chat's own 1,641,344 labelled commands), "
                       "and empty where no command was recorded (SWE-chat files those `other`); a result takes its "
                       "call's. Measured on the tool calls both hold (SWE-chat v2's sessions, aligned by session and "
                       "call id, 2026-10-05): `category` agrees on all 627,898 tool rows; `bash_category` on 98.9% of "
                       "145,211 shell calls, every difference on the same command: SWE-chat reads a leading "
                       "`NAME=value` as the command's first word (`other`), and files some commands by later words "
                       "(`mise run test` as `test/build`). SWE-chat files every tool result's `bash_category` `other`, "
                       "as a result has no command of its own; here a result takes its call's. SWE-chat files several "
                       "common tools `Other`, and so does this table: Claude Code's `Agent` (its renamed `Task`), "
                       "`TodoWrite`, `ToolSearch`, `AskUserQuestion`, `Skill`; Codex's `exec`, `shell_command`, "
                       "`write_stdin`, `update_plan`. SWE-chat's `user_id`, a commit author it resolved through GitHub, "
                       "is in no transcript and is not a column here.",
                       "- The conversation tables' text was scanned again as it reads once decoded, and every secret "
                       "found was masked in "
                       "the " + ("row and in the session's files." if strict else "row.")])
    sessions_without = []
    for source, c in sorted(conv.items()):
        why = ((changelog.get("conversations") or {}).get("tables") or {}).get(source, {}).get("sessions_without_rows") or {}
        sessions_without.append(f"| {source} | {c['conversations']:,} | {c['conversations_subagents']:,} | "
                                + (", ".join(f"{n:,} ({k})" for k, n in sorted(why.items(), key=lambda x: -x[1])) or "0")
                                + " |")
    lines += _details("Rows per source, and sessions without rows",
                      ["The key is (`session_key`, `turn_number`); `turn_id` is `<session_id>#<turn_number>`, or "
                       "`<session_key>#...` where the agent records no id. Duplicates have no rows: their kept copy "
                       "does.", "", "| Source | Rows | Subagent rows | Sessions without rows |", "|---|---|---|---|"]
                      + sessions_without)
    lines += _details("Session table columns in full",
                      ["One row per session (per file for Aider and SpecStory). `files` holds the files as published: "
                       "`path`, `bytes`, `sha256` and `content`. Only text is published: a file in another encoding or "
                       "a binary file is left out and listed in `source_facts` (`files_left_out_not_text`); a file with "
                       "a few broken UTF-8 sequences has them replaced by U+FFFD, noted in `source_facts` "
                       "(`text_repairs`). `content_sha256` is the digest of the files as fetched, before redaction; it "
                       "is how a refresh tells a changed session from an unchanged one. `fetched_at`, "
                       "`collector_version` and `content_sha256` describe a scraped session's own fetch: a copied "
                       "dataset is copied whole, so its sessions have them empty and carry the dataset and its revision "
                       "in `source_facts` (`dataset`, `revision`).", ""]
                      + [f"- `{name}`: {text}" for name, text in KEY_DEFINITIONS + COUNT_DEFINITIONS]
                      + ["- `repo_license`: GitHub's SPDX identification of the repository's licence; `none` when it "
                         "has no licence file; `unrecognized` when GitHub cannot identify it; empty when the "
                         "repository could not be looked up (gone, or private since). `repo_language`: GitHub's "
                         "primary language of the repository. For SWE-chat v2's sessions both are read from the "
                         "GitHub metadata SWE-chat recorded for the repository; DataClaw and OpenHands record no "
                         "repository, so both are empty. A copied dataset's own licence is in `source_facts` "
                         "(`dataset_license`)."
                         + (f" SWE-chat's own reading of the licence is kept in `source_facts` (`swechat_license_type`); "
                            f"it differs from GitHub's for {counts['swe-chat-v2']['licence_readings_differ']:,} of "
                            f"SWE-chat v2's {counts['swe-chat-v2']['kept']:,} kept sessions, mostly where GitHub cannot "
                            f"identify the licence file (`unrecognized`) or found none (`none`)."
                            if "swe-chat-v2" in counts else ""),
                         "- `source_facts`: facts specific to the source (for Entire: the checkpoint ids, the "
                         "repository the work was done in when it differs, whether the session was imported, Entire's "
                         "CLI version)."])
    sources_detail = [f"- **{title}**: {about}" for s, (title, about) in SCRAPED_ABOUT.items() if s in counts]
    sources_detail += ["", "Copied and linked datasets (a linked one's files are byte for byte another's, which is "
                       "copied):", "", "| Dataset | In this collection |", "|---|---|"]
    for name, info in sorted(copied.items()):
        n = info.get("rows", 0)
        rows = (f"copied: {n:,} row{'' if n == 1 else 's'} in its data files" if info.get("copied")
                else f"linked, not copied ({info.get('reason') or 'see its note'})")
        tag = "(copied files)" if info.get("copied") else "(linked)"   # apart from the sessions of the same name
        sources_detail.append(f"| {name} {tag} | {rows} |")
    sources_detail += ["", "A copied dataset is in the dataset viewer by the configs its own card declares; one that "
                       "declares none has a config only where its files are one kind, so a dataset of raw transcripts "
                       "in several shapes is listed and downloadable but not in the viewer.", ""] if hf else [""]
    sources_detail += [f"- **{name}**: {info.get('note', '')}" for name, info in sorted(copied.items())]
    lines += _details("How each source was collected", sources_detail)
    lines += _details("Duplicates by pair of sources",
                      [f"`{json.dumps(changelog.get('duplicate_pairs') or {})}`"])
    lines += _details("Sources left out, and why",
                      ["| Source | Why |", "|---|---|"] + [f"| {s} | {why} |" for s, why in OUT_OF_SCOPE])
    lines += ["## Versions", "", f"This version: `{changelog['version']}`, built {changelog['built_at']}. Each version is "
              + ("a dated tag" if hf else "a dated folder, with its checksum list beside it")
              + "; `changelog.json` gives the counts new and in total per source, the redactions by rule, the "
              "duplicates by pair of sources, and any fix made after the build.", ""]
    (version_dir / "README.md").write_text("\n".join(lines) + "\n")
    log(f"card: {sum(c['rows'] for c in counts.values()):,} scraped rows described")


def _licence_line(info: dict) -> str:
    """A copied dataset's licence line. hfcopy_build writes `<licence>, copied from <url> ...` for every dataset; a
    linked one's files are another's, so its line says linked."""
    line = info.get("license_line", "")
    return line if info.get("copied") else line.replace(", copied from ", ", linked, not copied: ", 1)


def _details(summary: str, body: list[str]) -> list[str]:
    """A collapsed section: its summary line shown, its body on a click (Hugging Face renders the Markdown inside)."""
    return ["<details>", f"<summary>{summary}</summary>", ""] + body + ["", "</details>", ""]

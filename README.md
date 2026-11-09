# PairLogs

An open pipeline that collects sessions between developers and AI coding agents from six public sources,
reads each source's format into one schema, removes duplicates across sources, masks secrets and names, marks which
messages a person typed, and releases the result in two tiers.

## Why

Coding agents such as Claude Code, Codex and Cursor log each session in detail: what the developer asked, what the
agent read, ran and changed, and how the developer reacted. Public logs of this kind exist, but they are scattered
across tools, repositories and datasets, each in its own format. And each log files more under "user" than what a
person typed: instruction files, environment details, tool output, other agents' messages, plan approvals.

Counting those as the developer's words changes what a study finds. In SWE-chat v2, a published dataset of such
sessions, about a third of its 201,485 labelled user prompts are text a program wrote. Measured only on what people
typed, its own pushback label is 55% for both Claude Code and Codex prompts; measured on every labelled prompt, the
two agents differ by 27 points (48% and 21%). `examples/swechat_gap.py` reproduces this from the released files.

Program text varies by agent in PairLogs too, even inside one logger (Entire's logs): programs wrote 16.8% of
what Cursor's logs file as the user's, 39.5% of Claude Code's and 53.2% of Codex's, so a pushback rate divided by every
user entry partly ranks the agents' harnesses (14.0% for Claude Code, 25.1% for Cursor). Over the typed entries the
gap shrinks but stays (25.2% and 31.3%), and the spread across agents halves. Claude Code's logs also mark the slash
commands people type (11.2% of its typed entries, 0.9% of them pushback); without them its gap to Cursor shrinks from
6.1 to 3.0 points, though other agents' commands may sit unmarked among their typed entries. `scripts/paper_e1/m17.py` computes this on a full version (tier B's
text rebuilt by the pipeline first: tier B's labels leave out each row's role and transcript entry).

## What the pipeline does

| Stage | What it does |
|---|---|
| Discover | Finds Entire session logs (GitHub commit search for the `Entire-Checkpoint:` trailer, a code search for Entire's settings file, and a seed list of repositories other datasets name), SpecStory and Aider chat histories (GitHub code search), and the copied datasets on Hugging Face. |
| Fetch | Reads each repository's logs with anonymous git, or a dataset's own files at a pinned revision. Raw files are kept as served. |
| Parse | One reader per log format (Claude Code, Codex, Cursor, Gemini CLI, Copilot, OpenCode, Pi, Aider, SpecStory's Markdown, and others) splits every message into rows with one set of columns. |
| De-duplicate | A session found in several places, or one conversation saved twice, is kept once (the fullest copy); the others stay as session rows with `duplicate_of`. |
| Mask | Secrets (gitleaks with calibrated exceptions), email addresses, user names inside home paths, and, in released text, people's full names found in typed messages, each wherever it occurs in the session. |
| Mark who typed | `typed_by_developer` on every row: from each agent's own records where it keeps them, else from fixed rules that err toward marking typed; prompts a script, bot or orchestrator sent into many sessions are the harness's, by their read openings; a helper agent's session filed as a session of its own (an OpenCode child, a session that opens with another session's request to a helper, or a DataClaw session run only on Claude Haiku) holds only the parent agent's messages, marked as the peer's (`collect briefs`). `actor` says who made each row: developer, agent, tool, harness (the agent program's own text) or peer (another agent). |
| Release | Tier A, the full text where licences allow it; tier B, pointers to every other session, rebuilt by this pipeline from its sources. |

## The release (version 2026-11-02)

| | Sessions | What you get |
|---|---|---|
| Tier A | 25,552 | The full text of sessions from repositories with a recognised open licence (Entire, SpecStory, Aider), and of the DataClaw uploads and OpenHands feedback (each tagged MIT on Hugging Face): 9.8 million message rows, raw files included. |
| Tier B | 36,057 | Pointers: repository, file hashes and, where known, the commit and checkpoint, for every other session. `collect update --only-file repositories.txt` rebuilds them from GitHub; SWE-chat v2's 3,730 come from SWE-chat's own release, under its terms. |

The sessions began between November 2023 and October 2026 and name 33 agents (some of them one product under several names). 44,555 of the 61,609
hold at least one message a person typed; the rest are scripted or unattended runs, among them 5,356 benchmark runs
from one Aider repository. Tier B redistributes no text whose repository carries no licence, and a session deleted
from its source is not rebuilt.

Each tier also holds `work_labels/`: each session's kind of task, and kind of project and domain, labelled by Claude
Haiku 4.5 from the guide in [docs/work-labels-guide.md](docs/work-labels-guide.md) (how, in its `meta.json`), and
`attribution/entire.parquet`: for 22,189 Entire sessions, Entire's own count of the committed lines the agent wrote
and those a person added, changed or removed ([docs/columns/attribution.md](docs/columns/attribution.md)). Tier A
holds `croissant.json`, a machine-readable description of its tables.

## Get the data

Version 2026-11-02 is on Hugging Face ([zanwenfu/PairLogs](https://huggingface.co/datasets/zanwenfu/PairLogs); an archive copy with a DOI is added here when it is made):

```bash
pip install -U huggingface_hub
hf download zanwenfu/PairLogs --repo-type dataset --local-dir PairLogs-2026-11-02
cd PairLogs-2026-11-02
sha256sum -c MEMBERS.sha256                    # every file (macOS: shasum -a 256 -c)
```

The folder is tier A (`data/`, `conversations/`, `conversations_subagents/`, `copied/`, `work_labels/`,
`attribution/`, `repositories.parquet`, `changelog.json`, `croissant.json`) and `tier-b/` (`pointers.parquet`,
`labels/`, `work_labels/`, `attribution/`, `repositories.parquet`, `repositories.txt`, `changelog.json`): about 21 GB. The examples below run
from this folder; `collect tiers` names the same two tiers `a/`
and `b/` when you make them yourself.

```bash
pip install duckdb
python examples/quickstart.py .                # sessions, agents, who wrote the user's rows, tools, one session
```

## Install

```bash
git clone <this repository> && cd errata-data
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'              # the pipeline and its tests
pip install -e '.[names]'             # the names stage (Presidio, spaCy 3.8.7)
python -m spacy download en_core_web_lg
```

Secret scanning needs gitleaks 8.30.1 on the path, or `ERRATA_GITLEAKS=/path/to/gitleaks`. GitHub discovery and
licence lookups need a token in `GITHUB_TOKEN`; copying gated Hugging Face datasets needs `HF_TOKEN`.

## Run

```bash
collect update                          # discover, fetch what is new, build a version under data/build/<date>
collect update --source entire --only owner/repo   # one repository, for a test
collect briefs --version-dir data/build/2026-11-02 --apply   # helper agents' sessions: their messages the parent's
collect check --version 2026-11-02      # scan the built files again for secrets and machine paths
collect tiers --version-dir data/build/2026-11-02 --out release/2026-11-02
collect names --tier-dir release/2026-11-02/a --out release/2026-11-02/a-names
```

To rebuild tier B's sessions from their sources:

```bash
collect update --only-file tier-b/repositories.txt
```

- No GitHub token is needed: the named repositories are read with anonymous git. Without one, an Entire session's
  commit is not recorded (it comes from GitHub's commit search), and licences are not looked up again.
- A session deleted from GitHub since the version was built is not rebuilt. Two tests, from a fresh copy of the code
  with no token (2026-11-04): 70 small repositories drawn at random (2 to 20 tier-B sessions each, 203 sessions in
  all): 200 came back, 199 with files identical to their pointers' hashes, and 2,162 of their 2,164 typed messages
  matched the label tables' text ids; 8 larger ones (4 Entire repositories of 100 MB to 1.5 GB of session files, the
  3 SpecStory repositories with the most sessions, and the largest Aider one besides a benchmark; 1,907 sessions):
  all 1,907 came back, 1,906 with identical files (the other had changed at its source), and all 6,676 typed
  messages matched. The 3 not rebuilt are in repositories deleted since the fetch.
- The known secret texts the release masks are not published, so a rebuilt file can still hold one that its
  repository still holds; the rebuild's own check scans what it makes.
- SWE-chat v2's 3,730 tier-B sessions come from SWE-chat's own release, under its terms, not from this list: accept
  its conditions on Hugging Face, set `HF_TOKEN`, and run `collect update --source swe-chat-v2`. That copies SWE-chat's
  current revision; version 2026-11-02 read revision 202b071f, so sessions SWE-chat changed since may differ.
- Time and memory: the test above took about 5 minutes. A full build of version 2026-11-02 (all six sources, at
  Duke) took 15 hours on 10 cores and peaked at 237 GB of memory; fetching all 3,603 of tier B's repositories again (the update test of 2026-11-06,
  fetching only) took about 5 hours on 8 cores.

Each run is incremental: a version is built from what is new or changed since the last one, and every number in a
version's `changelog.json` is counted from its stored rows.

## Read the data

The tables are Parquet; DuckDB, pandas or polars read them directly.

```python
import duckdb

duckdb.sql("""
    SELECT agent, count(*) AS typed_messages
    FROM 'conversations/*/*.parquet'
    WHERE typed_by_developer AND copy_of IS NULL
    GROUP BY agent ORDER BY typed_messages DESC
""").show()
```

| To get | Use |
|---|---|
| What developers typed | `typed_by_developer` and `copy_of` is null |
| What the agent did | `kind = 'tool_call'`, then `tool_name`, `category`, `command` |
| Failed tool calls | `is_error = true` |
| One row per distinct session | a session table where `duplicate_of` is null |
| The raw logs | `files` in the session tables |

Every column of every table is defined in [docs/columns](docs/columns/): the session tables (36 columns), and
`conversations` and `conversations_subagents` (50 columns each).

## How good the marks are

- The typed mark errs toward marking typed where a format records no author (SpecStory, Aider, DataClaw, unattended
  runs). Prompts that programs sent into many sessions are caught where their opening is shared by 10 or more
  sessions and was read (84 such openings, read on version 2026-11-01's rows); rarer ones are not. A person checked the mark on 184
  messages without seeing it: where both decided, they agree on 64 of SWE-chat v2's 75 labelled prompts (85%) and 66
  of our 84 (79%; 71 of 84 after this version's fix of what the check found, on the same messages). A second person,
  marking the same 184 messages on their own, agrees with the first on 174 of the 180 both decided (96.7%, kappa
  0.93). On a fresh draw of
  84 of our messages, weighted to the data, the person puts the typed mark's recall at 99.3% (95% interval 79.9 to
  100) and its precision at 81.6% (63.0 to 90.6); what it wrongly marks typed is mostly program text, so the program
  shares measured with the mark are, if anything, low. A model (Claude Sonnet 5.5) reading the same cards agrees with
  the person on 89.2% of them (kappa 0.76). A second person, marking the same 84 on their own, agrees with the first
  on 66 of the 78 both decided (84.6%, kappa 0.67) and puts the precision at 77.5% (58.9 to 87.8) and the recall at
  96.2% (77.9 to 96.8). Where logs record no author (SpecStory, Aider, DataClaw), text rules alone decide; on 90 more
  messages the mark calls typed there, 30 per source, the two people agree on 86 of the 87 both decided (98.9%, kappa
  0.90) and read 87% to 97% as typed in each source (weighted to the data, 91.8% and 89.4%); the rest is mostly
  program text.
- Masking catches most secrets, not every one: a password written out in prose is not found. Tier A as published is
  scanned again after masking (version 2026-11-02: 95 GB of text, 0 findings, two planted tokens found); that scan uses the masking's own rules, so it
  shows the masking ran everywhere, not that nothing was missed. The names stage masks people's full names found in
  typed messages (version 2026-11-02, tier A: 884 names, 23,520 occurrences in 4,070 rows, and 1,926 in the copied
  datasets' own files). Of 100 masks drawn at random among names found in one session only (on version 2026-10-11's
  list, read by an AI reader, not a person), 52 were people's names (many of them public: authors, researchers) and
  48 phrases or product names. A name that appears only in an agent's or a tool's text is left. A person read 99 of
  the name-shaped phrases the masking's own finder sees in 18,000 released tier-A rows (3,000 from each kind of
  writer) and found 42 to be people's names; tier A still holds a person's name in about 58,900 places (95% interval
  34,200 to 88,700), 87% of them in tools' output such as commit logs (a lower bound: the finder sees only full-name
  shapes).
- Discovery misses what GitHub's search misses, such as logs only on other branches. Of the 1,658 repositories with
  kept Entire sessions, the commit search found 1,511; SWE-chat's repository list adds 123 and the settings-file
  search 24. A two-sample estimate puts the public repositories holding Entire sessions at about 1,950 (1,877 to
  2,027), so at most about 85% are covered: both searches lean on GitHub's, so the estimate is likely low and the
  share likely lower. Our own searches alone find 1,535 (79%).

## Use and removal

We ask that the data be used for research, and that no one try to identify or contact the people in these sessions.
A session or repository is taken out of the next version on request: open an issue, or write to zanwen.fu@duke.edu,
naming it.

## Licence

The code is MIT. Each session's text keeps the licence of its source: its repository's (`repo_license` on each
session; tier A holds text only under a recognised open licence), or the copied dataset's own (DataClaw and OpenHands
feedback MIT; SWE-chat v2 ODC-By, not redistributed here). What the pipeline adds (the labels, the pointers, the
tables' layout and this documentation) is CC BY 4.0. Each version's `changelog.json` credits every copied dataset
with its source, revision, author and licence.

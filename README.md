# PairLogs

**Dataset:** [huggingface.co/datasets/zanwenfu/PairLogs](https://huggingface.co/datasets/zanwenfu/PairLogs) ·
**Datasheet:** [docs/datasheet.md](docs/datasheet.md) · **Columns:** [docs/columns](docs/columns/) ·
**Paper:** under review

This repository holds the code that builds PairLogs, a dataset of real sessions between developers and AI coding
agents, and the analysis scripts for its paper. The code finds public session logs and reads them into one schema. It
keeps each session once, masks secrets and names, and labels who wrote each message that the logs show as the user's.

## What PairLogs is

Coding agents such as Claude Code, Codex and Cursor log each session in detail: what the developer asked, what the
agent read, ran and changed, and how the developer reacted. Many developers publish these logs, but the logs are hard
to use as they are. Each tool writes its own format, one session is often saved in several places, and logs hold
secrets. Also, the "user" in a log is not always a person: the agent's own software adds text such as the repository's
instruction file. PairLogs labels each user message as typed by a person, a person's action (such as an interrupt), or
written by a program. By this label, a program wrote 32% of all user messages.

| | |
|---|---|
| Distinct sessions | 61,609 (53,022 of them from 3,602 GitHub repositories) |
| Sessions with a message a person typed | 44,555 |
| User messages in these sessions | 641,881 |
| Agents | Claude Code, Codex, Aider, Cursor, OpenCode and others: 33 agent names, some for the same product |
| Sources | Logs that developers committed to GitHub with the logging tools Entire and SpecStory, and Aider's chat files; three Hugging Face datasets: DataClaw uploads, OpenHands feedback and SWE-chat v2 |
| Sessions began | November 2023 to October 2026 |
| Current version | 2026-11-02, about 21 GB |

The release has two tiers. **Tier A** (25,552 sessions) includes the full text, for sessions whose source licence
allows sharing it. **Tier B** (36,057 sessions) includes all labels but no text: for each session it gives the
repository, the file hashes and, where known, the commit, so the text can be [rebuilt](#rebuild-tier-b).

## Get the data

Sign in to Hugging Face, open the [dataset page](https://huggingface.co/datasets/zanwenfu/PairLogs) and accept the
terms (no attempt to identify anyone; respect each source's licence). Access is granted at once. The download is about
21 GB.

```bash
pip install -U huggingface_hub duckdb
hf auth login                                      # a Hugging Face token with read access
hf download zanwenfu/PairLogs --repo-type dataset --local-dir PairLogs-data
git clone https://github.com/zanwenfu/PairLogs     # this repository, beside the data
cd PairLogs-data && sha256sum -c MEMBERS.sha256    # check each file (macOS: shasum -a 256 -c)
python ../PairLogs/examples/quickstart.py .        # prints counts per source and agent, who wrote the user messages, tool use and one example session
```

## Read the data

The tables are Parquet files; DuckDB, pandas or polars read them directly. From inside `PairLogs-data`:

```python
import duckdb
# messages people typed, per agent (tier A)
duckdb.sql("""
    SELECT agent, count(*) AS typed_messages
    FROM 'conversations/*/*.parquet'
    WHERE typed_by_developer AND copy_of IS NULL
    GROUP BY agent ORDER BY typed_messages DESC
""").show()
```

The main tables are `data/` (one row per session, with the raw log files), `conversations/` (one row per message, tool
call or tool result), `conversations_subagents/` (helper agents' conversations), `attribution/`, `work_labels/` and,
for tier B, `tier-b/`. The [dataset page](https://huggingface.co/datasets/zanwenfu/PairLogs#tables) lists them all.

| To get | Use |
|---|---|
| Who wrote a row | `actor`: developer, agent, tool, harness (text the agent's program inserts, such as instruction files) or peer (a message from another agent, such as a helper agent's report) |
| Messages people typed | `typed_by_developer` is true and `copy_of` is null (some agents log a message twice when it is typed while they work; `copy_of` marks the second copy) |
| A person's actions (an interrupt, a plan approval, an attached file) | `actor = 'developer'` and `typed_by_developer` is false |
| The agent's tool calls | rows with `kind = 'tool_call'`, with `tool_name` and, for shell tools, `command`; `is_error` marks failed tool outputs |
| One row per distinct session | `sessions` where `duplicate_of` is null (each copy points to its kept session) |

[docs/columns](docs/columns/) defines each column of each table.

## Label accuracy

| Label | Made by | Checked by | Result |
|---|---|---|---|
| Who wrote each user message (`typed_by_developer`, `actor`) | each agent's own records where it keeps them, otherwise fixed rules | two people, each on the same 84 messages, drawn per source and weighted by each source's share | of the messages people typed, it marks 99.3% and 96.2% (one figure per person) as typed; of the messages it marks typed, 81.6% and 77.5% really are, so about one in five is not |
| The same, where logs record no author (SpecStory, Aider, DataClaw) | fixed rules only | two people, 90 messages it marks typed, 30 per source | 87% to 97% really are typed, depending on the source |
| The same | | two people, 180 messages | they agree with each other on 96.7% (Cohen's kappa 0.93) |
| Pushback (`pushback`): a typed message corrects, redirects or rejects the agent's work, or reports it broken | OpenAI's gpt-6-luna, reading the message alone | two people, 80 typed messages | agrees with them on 87% and 82%; it marks 29% as pushback, the people 25% and 22%, and SWE-chat's broader label 55% |
| Kind of task, project and domain (`work_labels`) | Claude Haiku 4.5, from a written guide | one person, 100 items | the same main label on 58% to 65%: use these labels for proportions over many sessions, not to describe one session |

The [datasheet](docs/datasheet.md) describes each check, with its sample and 95% interval.

## Reproduce the paper's use case

- `examples/swechat_gap.py` computes, from released files only, SWE-chat v2's own pushback rate for Codex (21%) and
  Claude Code (48%) on all its labelled prompts, and on the prompts people typed (both about 55%).
- `scripts/paper_e1/` holds the paper's measurements: the same comparison with 95% intervals from resampling
  repositories, for each agent in SWE-chat (`m16.py`, `m16b.py`); agents compared within one logging tool (`m17.py`);
  and the figures (`figs.py`). Its [README](scripts/paper_e1/README.md) lists each script.

## Install

```bash
git clone https://github.com/zanwenfu/PairLogs && cd PairLogs
python -m venv .venv && . .venv/bin/activate         # Python 3.11 or newer
pip install -e '.[test]'                             # the pipeline and its tests
pytest -q tests                                      # 470 tests; the secret-scan tests are skipped without Gitleaks
pip install -e '.[names]'                            # the name-masking stage (Presidio, spaCy 3.8.7)
python -m spacy download en_core_web_lg
```

Secret scanning needs Gitleaks 8.30.1 on the path, or `ERRATA_GITLEAKS=/path/to/gitleaks`. GitHub search and licence
lookups need a token in `GITHUB_TOKEN`; copying gated Hugging Face datasets needs `HF_TOKEN`. A full build of all six
sources took 15 hours on 10 cores and up to 237 GB of memory. The pipeline
calls no language model; the two model labels were made by the scripts in `scripts/pushback/` and
`scripts/paper_e1/work_labels*.py`, which need their own model access.

## Run the pipeline

```bash
collect update                                       # find, fetch and build what is new, as a version under data/build/<date>
collect update --source entire --only owner/repo     # one repository, for a test
collect briefs --version-dir data/build/<date> --apply   # in helper-agent sessions, mark the "user" messages as the parent agent's
collect check --version <date>                       # scan the built files again for secrets and local paths with user names
collect tiers --version-dir data/build/<date> --out release/<date>         # write tier A and tier B
collect names --tier-dir release/<date>/a --out release/<date>/a-names     # mask people's full names in tier A
```

| Stage | What it does |
|---|---|
| Find | Entire logs (GitHub commit search for commits whose message ends with the line `Entire-Checkpoint:`, which the Entire tool adds; code search for Entire's settings file; the repositories SWE-chat names), SpecStory and Aider chat histories (code search), and the copied datasets on Hugging Face. |
| Fetch | Each repository with anonymous git, or a dataset's own files at a fixed revision. Raw files are stored unchanged. |
| Read | One reader per log format (Claude Code, Codex, Cursor, Gemini CLI, Copilot, OpenCode, Pi, Aider, SpecStory's Markdown, and others) writes one row per session and one per message, tool call or tool result. |
| Deduplicate | A session found in several places is kept once (the most complete copy); the other copies stay as session rows that point to it (`duplicate_of`). |
| Mask | Secrets; in the sources we collect, email addresses and user names in file paths (copied datasets keep their publishers' handling); in released text, people's full names found in typed messages, wherever they occur in the session. |
| Label authorship | Who wrote each row (`actor`) and whether a person typed it (`typed_by_developer`): from each agent's own records where it keeps them, otherwise from fixed rules. |
| Release | Tier A, the full text where licences allow it; tier B, pointers for the other sessions. |

Each run adds only what is new or changed since the last version. The counts in a version's `changelog.json` are
computed from that version's own files.

## Rebuild tier B

```bash
collect update --only-file PairLogs-data/tier-b/repositories.txt
```

- No GitHub token is needed: the repositories are read with anonymous git. Without a token, an Entire session's commit
  is not recorded and licences are not looked up again.
- The rebuilt text goes through the same secret masking, except the private list of known secret strings; run
  `collect names` to mask names. Join the rebuilt rows to tier B's labels by `text_id`, the hash of each typed message
  ([docs/columns/labels.md](docs/columns/labels.md)).
- Tested from a fresh copy of the code with no token (and the build's own masking lists): of 2,110 tier-B sessions (70 small repositories drawn at random
  and 8 large ones), 2,107 were rebuilt, and 2,105 of them match their stored hashes exactly; the 3 others are in
  repositories deleted since. Fetching all 3,603 GitHub repositories of the version again (both tiers) took about 5 hours on 8 cores.
- SWE-chat v2's 3,730 tier-B sessions come from SWE-chat's own release: accept its terms on Hugging Face, set
  `HF_TOKEN`, and run `collect update --source swe-chat-v2`.

## Repository layout

| Folder | What is in it |
|---|---|
| `collect/` | the pipeline: finding, reading, deduplication, masking, authorship labels, release |
| `docs/` | the datasheet, the columns, the work-labels guide, the dataset card |
| `examples/` | a quick start, and the use case from released files |
| `scripts/` | the paper's measurements, the checks with people, the model labels, release and rebuild tests |
| `tests/` | the pipeline's tests |

## Terms of use and removal

Please use the data for research, and do not try to identify or contact the people in these sessions. To have a
session or repository removed from the next version, open an issue or write to zanwen.fu@duke.edu, naming it.

## Licence

The code is MIT. Each session's text keeps its source's licence: its repository's (`repo_license` on each session) or
that of the Hugging Face dataset it came from (DataClaw and OpenHands feedback: MIT; SWE-chat v2: ODC-By). Tier A holds
text only from repositories with a recognised open licence, and SWE-chat v2's sessions are not redistributed. What the
pipeline adds (labels, pointers, the tables' layout and this documentation) is CC BY 4.0. Each version's
`changelog.json` credits each copied dataset with its source, revision, author and licence.

## Cite

See [CITATION.cff](CITATION.cff), or:

```bibtex
@misc{pairlogs2026,
  title        = {{PairLogs}: A Multi-Source Dataset of Developer--Agent Sessions with Message Authorship},
  author       = {Fu, Zanwen},
  year         = {2026},
  howpublished = {Hugging Face dataset, version 2026-11-02},
  url          = {https://huggingface.co/datasets/zanwenfu/PairLogs}
}
```

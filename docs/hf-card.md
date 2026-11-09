---
pretty_name: PairLogs
license: other
license_name: per-source
license_link: LICENSE.md
language:
- en
size_categories:
- 10K<n<100K
task_categories:
- text-generation
tags:
- coding-agents
- software-engineering
- mining-software-repositories
- claude-code
- codex
- agent-traces
configs:
- config_name: sessions
  default: true
  data_files: "data/*/*.parquet"
- config_name: conversations
  data_files: "conversations/*/*.parquet"
- config_name: conversations_subagents
  data_files: "conversations_subagents/*/*.parquet"
- config_name: work_labels_sessions
  data_files: "work_labels/sessions.parquet"
- config_name: work_labels_repositories
  data_files: "work_labels/repositories.parquet"
- config_name: repositories
  data_files: "repositories.parquet"
- config_name: attribution
  data_files: "attribution/entire.parquet"
- config_name: tier_b_pointers
  data_files: "tier-b/pointers.parquet"
- config_name: tier_b_message_labels
  data_files: "tier-b/labels/conversations/*/*.parquet"
- config_name: tier_b_swe_chat_prompts
  data_files: "tier-b/labels/swe_chat_prompts.parquet"
- config_name: tier_b_work_labels_sessions
  data_files: "tier-b/work_labels/sessions.parquet"
- config_name: tier_b_attribution
  data_files: "tier-b/attribution/entire.parquet"
---

# PairLogs: developer–agent sessions with message authorship

Sessions between developers and AI coding agents (Claude Code, Codex, Cursor, Aider, OpenCode and others), collected
from six public sources, read into one schema, with copies across sources removed, secrets and personal details
masked, and every message marked with who wrote it. Version 2026-11-02.

| | |
|---|---|
| Distinct sessions | 61,609 (53,022 from 3,602 GitHub repositories) |
| With a message marked typed by a person | 44,555 |
| Tier A: full text (this repository) | 25,552 sessions, 9.8 million message rows, raw files included |
| Tier B: pointers (`tier-b/`) | 36,057 sessions, every label without text; the pipeline rebuilds the text from its source |
| Sources | Entire, SpecStory and Aider logs committed to GitHub; DataClaw, OpenHands feedback and SWE-chat v2 on Hugging Face |
| Sessions began | November 2023 to October 2026 |

## Why it exists

Agents' logs file far more under "user" than what a person typed: instruction files, tool output, other agents'
messages, prompts sent by scripts. PairLogs marks which messages a person typed (`typed_by_developer`, and `actor`:
developer, agent, tool, harness or peer). The mark changes what earlier data shows: in SWE-chat v2, about a third of
its 201,485 labelled user prompts are text a program wrote, and its own pushback label, 27 points apart for Claude
Code and Codex on every prompt, is the same for both on the prompts people typed (`examples/swechat_gap.py` in the
code repository reproduces this from the released files).

Program text varies by agent in PairLogs too, even inside one logger (Entire's logs): programs wrote 16.8% of
what Cursor's logs file as the user's, 39.5% of Claude Code's and 53.2% of Codex's, so a pushback rate divided by every
user entry partly ranks the agents' harnesses (14.0% for Claude Code, 25.1% for Cursor). Over the typed entries the
gap shrinks but stays (25.2% and 31.3%), and the spread across agents halves. Claude Code's logs also mark the slash
commands people type (11.2% of its typed entries, 0.9% of them pushback); without them its gap to Cursor shrinks from
6.1 to 3.0 points, though other agents' commands may sit unmarked among their typed entries. The typed rates compare the developers who use each agent, not the
agents' effect.

## Get it

```bash
pip install -U huggingface_hub duckdb
hf download zanwenfu/PairLogs --repo-type dataset --local-dir PairLogs-2026-11-02
cd PairLogs-2026-11-02 && sha256sum -c MEMBERS.sha256       # every file (macOS: shasum -a 256 -c)
```

```python
import duckdb
duckdb.sql("""
    SELECT agent, count(*) AS typed_messages
    FROM 'conversations/*/*.parquet'
    WHERE typed_by_developer AND copy_of IS NULL
    GROUP BY agent ORDER BY typed_messages DESC
""").show()
```

Each table is also a configuration of this repository (`datasets.load_dataset("zanwenfu/PairLogs", "conversations")`).

## What is here

Tier A, at the top: `data/<source>/` (one row per session: 36 columns, the raw files in `files`),
`conversations/<source>/` and `conversations_subagents/<source>/` (one row per message, tool call or tool result: 50
columns), `copied/` (the copied datasets' own files, masked), `work_labels/`, `attribution/` (Entire's count of the
committed lines its agent wrote, per session), `repositories.parquet` (each repository's
owner type), `changelog.json` (every count, every copied dataset credited with its source, revision, author and
licence) and `croissant.json`. Tier B, in `tier-b/`: `pointers.parquet` (repository, file hashes and,
where known, the commit and checkpoint), `labels/` (each message row's labels and a digest of its text, and who wrote each of SWE-chat's labelled
prompts), `work_labels/`, `repositories.txt` and its changelog. Every column is defined in the code repository's
`docs/columns`, and how the data was made and checked in its `docs/datasheet.md`.

| To get | Use |
|---|---|
| What developers typed | `typed_by_developer` and `copy_of` is null |
| What the agent did | `kind = 'tool_call'`, then `tool_name`, `category`, `command` |
| Failed tool calls | `is_error = true` |
| How much of the committed code the agent wrote (Entire sessions) | `attribution/entire.parquet`, Entire's own count |
| One row per distinct session | a session table where `duplicate_of` is null |

## Labels, and how they were checked

- **Who typed it** (the pipeline's rules and each agent's own records). A person checked 184 messages:
  SWE-chat's labelled prompts 64 of 75, our own rows 66 of 84 where both decided (71 of 84 after this version's fix
  of what the check found, on the same messages, so not an independent measure). A second person, marking the same
  184 messages on their own, agrees with the first on 174 of the 180 both decided (96.7%, kappa 0.93). The check found helper agents'
  sessions filed as sessions of their own, whose "user" messages are the parent agent's; they are marked so in this
  version. On a fresh draw of 84 of our messages, weighted to the data, the person puts the typed mark's
  recall at 99.3% (95% interval 79.9 to 100) and its precision at 81.6% (63.0 to 90.6); what it wrongly marks typed
  is mostly program text. A model reading the same cards agrees with the person on 89.2% of them (kappa 0.76); a second person agrees with the first on 84.6% (kappa 0.67) and puts the precision at 77.5% and the recall at 96.2%. Where a format records no author (SpecStory, Aider, DataClaw), text rules alone decide: on 90 more messages the mark calls typed there, 30 per source, two people (agreeing on 98.9%, kappa 0.90) read 87% to 97% as typed in each source; the rest is mostly program text.
- **Pushback** (`pushback`, gpt-6-luna, the message alone): whether a typed message corrects, redirects, rejects or
  reports broken the agent's work. A person's check of 80 messages: the person 25%, this label 29% (accuracy 0.87);
  SWE-chat's own, broader label 55%. A second person agrees with the first on 61 of 68 (kappa 0.72) and puts this
  label's accuracy at 0.82.
- **A second reader** (a model, Claude Sonnet 5.5, given the same guide and the same text the person saw, never an
  answer): on who typed it, it agrees with the person on 166 of 179 messages where both decided (kappa 0.85); on
  pushback, 62 of 68 (kappa 0.77).
- **Kind of task, project and domain** (`work_labels/`, Claude Haiku 4.5 from a written guide): a person's check of
  100 items gives the same main label on 65%, 65% and 58%. Use the shares, not single labels.

## Privacy

Secrets are masked (gitleaks with calibrated exceptions, plus known texts), and so are email addresses and user names in
home paths; in tier A, people's full names found in typed messages are masked wherever they occur in the session. Every
published file was scanned again after masking, and tier A also by a second, independent scanner (TruffleHog): before
masking it flagged 28,206 distinct strings, nearly all ids, hashes and pieces of encoded data (none was tried against a
live service). We masked the 325 that could be keys or passwords (a provider's key format, a connection string's
password, a value right after a key or password name, a key-like string with digits, each in every written form found,
and three encoded blocks holding two API keys) in 2,500 places. Scanned again, the only masked strings it still reports
are four it assembles from separate settings, none of them a key. Masking catches most secrets, not every one. A
name only in an agent's reply or a tool's output is left: by a person's reading of a sample, tier A still holds a person's
name in about 58,900 places (95% interval 34,200 to 88,700), 87% of them in tools' output such as commit logs. File
names stay as their repositories publish them: SpecStory names a session's file by the chat's title, so session keys and
paths in both tiers carry that title. We ask that the data be used for research and that no one try to identify or
contact the people in these sessions. Before release we read every GitHub repository again (2026-11-06): the 397
sessions whose repository had since been deleted, made private or emptied by its owner (18 repositories) keep no text
here and are pointers in tier B. A session or repository is removed from the next version on request: write to
zanwen.fu@duke.edu, naming it.

## Licence

Each session's text keeps the licence of its source: its repository's (`repo_license`; tier A holds text only under a
recognised open licence), or the copied dataset's own (DataClaw and OpenHands feedback MIT; SWE-chat v2's sessions are
not redistributed here). What the pipeline adds (the labels, the pointers, the tables' layout and this documentation)
is CC BY 4.0. The code is MIT.

## Known limits

The data is what public search finds, not a sample of developers; a few repositories hold many sessions; about 0.9% of
kept sessions miss a part of their work that another file under the same session id holds (`also_in` says where);
pointers decay as repositories are deleted; the labels are models' readings of short texts.

## Cite

```bibtex
@misc{pairlogs2026,
  title        = {{PairLogs}: A Multi-Source Dataset of Developer--Agent Sessions with Message Authorship},
  author       = {Fu, Zanwen},
  year         = {2026},
  howpublished = {Hugging Face dataset, version 2026-11-02},
  url          = {https://huggingface.co/datasets/zanwenfu/PairLogs}
}
```

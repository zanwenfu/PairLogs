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
extra_gated_prompt: "PairLogs holds real developers' sessions. To get access, you agree not to try to identify or contact the people in these sessions, and to respect each session's source licence (see Licence on this page). We ask that the data be used for research. This dataset does not collect requesters' Hugging Face usernames or emails."
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

# PairLogs

**Code:** [github.com/zanwenfu/PairLogs](https://github.com/zanwenfu/PairLogs) ·
**Datasheet:** [docs/datasheet.md](https://github.com/zanwenfu/PairLogs/blob/main/docs/datasheet.md) ·
**Columns:** [docs/columns](https://github.com/zanwenfu/PairLogs/tree/main/docs/columns) · **Paper:** under review

PairLogs is a dataset of real sessions between developers and AI coding agents such as Claude Code, Codex, Cursor,
Aider and OpenCode. It gathers public sessions from six sources into one schema.

In agent logs, many messages shown as the user's were not typed by a person: the agent's own software adds text such as
the repository's instruction file. PairLogs labels who wrote each user message: a person typing, a person's action
(such as an interrupt), or a program. By this label, a program wrote 32% of all user messages.

A session saved in several places is kept once, secrets and names are masked, and the pipeline adds new sessions when
it is run again. This page describes version 2026-11-02 (November 2026).

## Example: authorship changes a published result

SWE-chat v2, an earlier dataset, labels its user prompts for pushback. A program wrote about a third of its 201,485
labelled prompts. On all prompts, SWE-chat's own label suggests that developers push back on Codex less than half as
often as on Claude Code (21% vs 48%). On the prompts people typed, both are about 55%. The script
`examples/swechat_gap.py` in the code repository reproduces this from released files.

## At a glance

| | |
|---|---|
| Distinct sessions | 61,609 (53,022 of them from 3,602 GitHub repositories); copies are not counted |
| Sessions with a message a person typed | 44,555 |
| User messages in these sessions | 641,881 |
| Rows of messages, tool calls and tool results, both tiers | 19.9 million |
| Agents | Claude Code (25,171 sessions), Codex (9,450), Aider (5,948, of which 5,356 are scripted runs in one repository), Cursor (4,156), OpenCode (3,282) and others: 33 agent names, some for the same product |
| Sources | Logs that developers committed to GitHub with the logging tools Entire and SpecStory, and Aider's chat files; three Hugging Face datasets: DataClaw uploads, OpenHands feedback and SWE-chat v2 |
| Sessions began | November 2023 to October 2026 |
| Size | about 21 GB |

## Two tiers

**Tier A** (25,552 sessions, in the top-level folders) includes the full text, for sessions whose source licence allows
sharing it. **Tier B** (36,057 sessions, in `tier-b/`) includes all labels but no text. For each session it gives where
to fetch the text again: the repository, the file hashes and, where known, the commit. The pipeline rebuilds tier B's
text from GitHub without a GitHub token; fetching all of tier B's repositories again took about 5 hours on 8 cores. SWE-chat v2's 3,730
tier-B sessions come from SWE-chat's own release instead ([how to rebuild](https://github.com/zanwenfu/PairLogs#rebuild-tier-b)).

## Access and download

Sign in to Hugging Face, open this page and accept the terms. Access is granted at once, and your username and email
are not collected.

```bash
pip install -U huggingface_hub duckdb
hf auth login                                     # a Hugging Face token with read access
hf download zanwenfu/PairLogs --repo-type dataset --local-dir PairLogs-data
cd PairLogs-data && sha256sum -c MEMBERS.sha256   # check each file (macOS: shasum -a 256 -c)
```

To download one table only, add for example `--include "data/*"` (sessions) or `--include "conversations/*"`.

```python
import duckdb   # run inside PairLogs-data; this reads tier A
# messages people typed, per agent
duckdb.sql("""
    SELECT agent, count(*) AS typed_messages
    FROM 'conversations/*/*.parquet'
    WHERE typed_by_developer AND copy_of IS NULL
    GROUP BY agent ORDER BY typed_messages DESC
""").show()
```

You can also load each table by name with the `datasets` library, for example
`load_dataset("zanwenfu/PairLogs", "sessions")`. The `conversations` table has about 9.8 million rows.

## Tables

| Name | One row per | Use it for |
|---|---|---|
| `sessions` (folder `data/`) | session; 36 columns, raw log files included | agents, repositories, dates, licences |
| `conversations` | message, tool call or tool result; 50 columns | what people typed, what agents did, `pushback` |
| `conversations_subagents` | row of a helper agent's conversation | work an agent handed to a helper agent |
| `attribution` | session logged by Entire (9,512; 12,677 more in tier B) | which committed lines the agent wrote, as Entire records them |
| `work_labels_sessions`, `work_labels_repositories` | session; repository | kind of task, project and domain |
| `repositories` | repository | whether the owner is a person or an organisation |
| `tier_b_pointers` | tier B session | fetching the text again |
| `tier_b_message_labels` | tier B message row | its labels, and a hash of each typed message to check rebuilt text |
| `tier_b_swe_chat_prompts` | SWE-chat v2 labelled prompt (201,485; text not included) | who wrote it |
| `tier_b_work_labels_sessions`, `tier_b_attribution` | tier B session | as in tier A |

Tables join on `session_key`. The columns most analyses need:

| To get | Use |
|---|---|
| Who wrote a row | `actor`: developer, agent, tool, harness (text the agent's program inserts, such as instruction files) or peer (a message from another agent, such as a helper agent's report) |
| Messages people typed | `typed_by_developer` is true and `copy_of` is null (some agents log a message twice when it is typed while they work; `copy_of` marks the second copy) |
| A person's actions (an interrupt, a plan approval, an attached file) | `actor = 'developer'` and `typed_by_developer` is false |
| The agent's tool calls | rows with `kind = 'tool_call'`, with `tool_name` and, for shell tools, `command`; `is_error` marks failed tool outputs |
| One row per distinct session | `sessions` where `duplicate_of` is null (each copy points to its kept session) |

## Label accuracy

| Label | Made by | Checked by | Result |
|---|---|---|---|
| Who wrote each user message (`typed_by_developer`, `actor`) | each agent's own records where it keeps them, otherwise fixed rules | two people, each on the same 84 messages, drawn per source and weighted by each source's share | of the messages people typed, it marks 99.3% and 96.2% (one figure per person) as typed; of the messages it marks typed, 81.6% and 77.5% really are, so about one in five is not |
| The same, where logs record no author (SpecStory, Aider, DataClaw) | fixed rules only | two people, 90 messages it marks typed, 30 per source | 87% to 97% really are typed, depending on the source |
| The same | | two people, 180 messages | they agree with each other on 96.7% (Cohen's kappa 0.93) |
| Pushback (`pushback`): a typed message corrects, redirects or rejects the agent's work, or reports it broken | OpenAI's gpt-6-luna, reading the message alone | two people, 80 typed messages | agrees with them on 87% and 82%; it marks 29% as pushback, the people 25% and 22%, and SWE-chat's broader label 55% |
| Kind of task, project and domain (`work_labels`) | Claude Haiku 4.5, from a written guide | one person, 100 items | the same main label on 58% to 65%: use these labels for proportions over many sessions, not to describe one session |

The [datasheet](https://github.com/zanwenfu/PairLogs/blob/main/docs/datasheet.md) describes each check, with its sample and 95% interval.

## Privacy

We used only logs that were public before we fetched them, and we contacted no one. Secrets are masked by three
methods: Gitleaks with rules tuned on real logs, rules for known key formats, and a private list of known secret
strings. Email addresses and user names in file paths are masked too. In tier A, people's full names found in
typed messages are masked wherever they occur in the session. After masking, a second scanner (TruffleHog) read tier A;
the 4 strings it still flags are built from separate settings, not keys. We have not measured how many secrets both
scanners miss. A name that appears only in an agent's reply or a tool's output (such as a commit log) is not masked.
SpecStory names each file after the chat's title, so some session IDs (`session_key`) and paths contain that title. If
a log was deleted from its source before a release was built, that release does not contain its text.

## Terms of use and removal

Please use the data for research, and do not try to identify or contact the people in these sessions. To have a
session or repository removed from the next version, open an issue on [GitHub](https://github.com/zanwenfu/PairLogs/issues) or write to
zanwen.fu@duke.edu, naming it.

## Licence

Each session's text keeps its source's licence: its repository's (`repo_license`) or that of the Hugging Face dataset
it came from (DataClaw and OpenHands feedback: MIT; SWE-chat v2: ODC-By). Tier A holds text only from repositories with
a recognised open licence, and SWE-chat v2's sessions are not redistributed here ([LICENSE.md](LICENSE.md)). What the
pipeline adds (labels, pointers, the tables' layout and this documentation) is CC BY 4.0. `changelog.json` credits each
copied dataset with its source, revision, author and licence.

## Limits

The data is what public search finds, from developers who chose to publish their logs, so it does not represent all
developers. A few repositories hold many sessions. Tier B sessions cannot be rebuilt once their repository is deleted:
in a test, 3 of 2,110 already could not.

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

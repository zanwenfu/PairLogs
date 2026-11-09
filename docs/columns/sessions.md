# Session table columns

`data/<source>/*.parquet`: one row per session (per file for Aider and SpecStory), one folder per source, all with the same 36 columns. A session found in several places has a row for each; `duplicate_of` marks the copies set aside, which have no files and no rows in the conversation tables. A copy is the same session under the same id or bytes, or one conversation stored again under another id: its typed messages in order are a fuller session's of the same repository or copied export (or open that session's longer run), and its agent's replies and tool calls are mostly the same, or its typed side is too long to be chance. The fuller one is kept. `session_key` links a session to its rows there.

"Empty" means null. Types are Parquet's: `string`, `text` (a string that can be long), `int`, `bool` and `timestamp` (UTC), and lists or records where shown.

| Column | Type | Description | Notes |
|---|---|---|---|
| `session_key` | string | `<source>:<source_id>`, unique across the dataset. | Links to the conversation tables. |
| `source` | string | The source. |  |
| `source_id` | string | The session's id within its source, such as a repository and file path, or a dataset record. |  |
| `session_id` | string | The agent's own session id. | Empty for Aider and OpenHands feedback, and for about half of SpecStory's sessions. |
| `checkpoint_pk` | string | Entire's `<repository>#<checkpoint id>`. | Entire and SWE-chat v2 only. |
| `strategy` | string | How Entire saved the session. | Entire and SWE-chat v2 only. |
| `dedup_key` | string | The key duplicates are matched on: `<agent>:<session id>` where recorded, else `sha256:<digest of the files>`. |  |
| `found_url` | string | Where the session was found: a GitHub commit or file, or a dataset. |  |
| `found_commit` | string | The commit the files were read from: a repository's commit, or a copied dataset's revision. | Empty for about a quarter of Entire's sessions. |
| `fetched_at` | string | When the files were fetched (ISO 8601, UTC). | Empty for copied datasets. |
| `collector_version` | string | The collector's version that fetched them. | Empty for copied datasets. |
| `content_sha256` | string | A digest of the files as fetched, before masking. | Empty for copied datasets. |
| `repo` | string | The GitHub repository, as `owner/name`. | Empty for DataClaw and OpenHands. |
| `repo_url` | string | The repository's URL. |  |
| `repo_license` | string | GitHub's reading of the repository's licence: an SPDX id, `none` (no licence file) or `unrecognized`. | Empty where the repository could not be looked up. |
| `repo_language` | string | GitHub's main language of the repository. |  |
| `agent` | string | The coding agent. | Empty where the files do not name it. |
| `agent_version` | string | The agent's version. | Not recorded for DataClaw, OpenHands feedback or SpecStory. |
| `models` | list of strings | The models the session used. |  |
| `started_at` | string | The first message's time (ISO 8601, UTC). | Empty where the files record no time with a time zone (every Aider session); a time without one is kept in `source_facts`. |
| `ended_at` | string | The last message's time (ISO 8601, UTC). | Empty where the files record no time with a time zone (every Aider session); a time without one is kept in `source_facts`. |
| `file_format` | string | `jsonl`, `json`, `markdown` or `parquet`. |  |
| `n_user_entries` | int | Every entry in the user's role, typed or not. | For Claude Code this includes tool outputs. |
| `n_user_messages` | int | Messages the developer typed, each counted once (`typed_by_developer` rows, copies left out). | Empty where the session's files were not read. |
| `n_agent_messages` | int | The agent's messages with text. | For Claude Code, distinct API messages, its `<synthetic>` replies left out. |
| `n_tool_calls` | int | The agent's tool calls. | 0 for Aider, whose edits are not tool calls. |
| `has_tool_results` | bool | Whether the files record what tools returned. | Empty where the session's files were not read. |
| `has_timestamps` | bool | Whether the files record times. |  |
| `n_conversation_rows` | int | The session's rows in `conversations`. | Empty for a duplicate, and where the session's files were not read (no reader for its agent, or no file); 0 where they were read and gave no rows. `source_facts` (`conversations_error`) says why. |
| `n_subagent_rows` | int | The session's rows in `conversations_subagents`. |  |
| `bytes` | int | The total size of the session's files. |  |
| `duplicate_of` | string | For a duplicate, the `session_key` of the copy that was kept. | A duplicate has no files and no rows. A near-copy (one conversation stored again) is a duplicate of the fuller session. |
| `also_in` | list of `{source, source_id, found_url}` | Every other place the session was found, near-copies included. |  |
| `redactions` | `{secrets, emails, home_users}` | How many secrets, emails and home-folder names were masked in its files. |  |
| `source_facts` | text (JSON) | Details only some sources have, such as Entire's checkpoint ids and CLI version, a copied dataset's name, revision and licence, and `typed_fingerprint`, a digest of the typed messages in order. | Keys differ by source. |
| `files` | list of `{path, bytes, sha256, content}` | The session's raw files, masked. |  |

Other tables: [`conversations`](conversations.md), [`conversations_subagents`](conversations_subagents.md). The [README](../../README.md) says where the data is and how to get it.

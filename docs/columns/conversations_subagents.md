# `conversations_subagents` columns

`conversations_subagents/<source>/*.parquet`: the rows of helper agents (subagents) a session started, kept apart from `conversations` as SWE-chat does, one row per piece of a helper's messages, in order. Only Entire's and SWE-chat v2's sessions record them: 91,967 rows in version 2026-10-04, from Claude Code and Copilot CLI.

The table has the columns of `conversations` less `queue_op_subtype`, `copy_of` and `pushback` (a helper agent queues no messages, and nobody types to one), with three of its own first. Each row carries its parent session's ids.

"Empty" means null. Types are Parquet's: `string`, `text` (a string that can be long), `int`, `bool` and `timestamp` (UTC).

| Column | Type | Description | Notes |
|---|---|---|---|
| `task_pk` | string | The helper agent's task: `<checkpoint_pk>#<id>`, the id being that of the tool call that started it, else the helper's own id (`sidechain` if it has none). | Without a checkpoint it starts with `<session_key>`; every row in this version has one. |
| `turn_pk` | string | `<task_pk>#<turn_number>`. | Unique together with `session_key`: 4 helper threads are kept in the files of two sessions of the same checkpoint and appear under both (906 rows). |
| `agent_id` | string | The helper agent's own id, as its files give it (Claude Code: `agent-<id>`, its file's name); where they give none, the id of the call that started it, or `sidechain`. |  |
| `turn_id` | string | `<session_id>#<turn_number>`, numbered within the helper's own rows. | Repeats `turn_id` values of the parent's rows in `conversations`, as in SWE-chat: use `turn_pk`. |
| `session_id` | string | The parent session's id. |  |
| `checkpoint_pk` | string | The parent session's checkpoint, `<repository>#<checkpoint id>`. |  |
| `repo_id` | string | The parent session's repository, as `owner/name`. |  |
| `turn_number` | int | The row's position in the helper's own rows, from 0. | With `session_key` and `task_pk`, it identifies the row. |
| `conversation_turn_number` | int | The position among the helper's dialogue rows (its prompt and its replies), from 0. | Empty on other rows. |
| `source_entry_uuid` | string | The id of the log entry the row came from. | Where the format records one. |
| `response_id` | string | The id of the model's API response. | Where the format records one. |
| `content_block_index` | int | Which piece of its message the row is, from 0. |  |
| `role` | string | SWE-chat's role: `user` (the helper's prompt), `assistant`, `tool_use`, `tool_result`, `metadata`. | Who wrote the row is in `actor`. |
| `turn_type` | string | SWE-chat's row type. Here: `user_prompt` (the helper's prompt), `assistant_response`, `assistant_thinking`, `tool_use`, `tool_result`, `progress`, `system_event`. |  |
| `is_conversational` | bool | `true` for prompts and replies (`user_prompt`, `assistant_response`). |  |
| `content` | text | The row's text, with secrets as `[REDACTED]`. Images and documents appear as `[Image: image/png]`. | A tool's output over 100,000 characters is cut; see `content_truncated`. |
| `model` | string | The model that wrote the row. | Agent rows. |
| `timestamp` | timestamp | When the log entry was written. | Empty where the format records no time. |
| `input_tokens` | int | The model call's input tokens. | Once per call, on its first text row (else its first tool call). Anthropic models count without cache reads; OpenAI's (Codex) include cached input. |
| `output_tokens` | int | The model call's output tokens. | Placed as `input_tokens`. |
| `cache_creation_input_tokens` | int | Tokens the call wrote to the prompt cache. | Placed as `input_tokens`. |
| `cache_read_input_tokens` | int | Tokens the call read from the prompt cache. | Placed as `input_tokens`. |
| `is_continuation` | bool | `true` on a user row opening with Claude Code's "This session is being continued..." summary. | Always `false` in this table. |
| `is_first_turn` | bool | `true` on the helper's first dialogue row. |  |
| `word_count` | int | Words in `content`, split on whitespace. |  |
| `char_count` | int | Characters in `content`. |  |
| `tool_name` | string | The tool called, as the agent names it. | A tool's output carries its call's. |
| `tool_call_id` | string | The call's id. | A tool's output carries its call's, which pairs the two. |
| `file_path` | string | The file a tool call works on. | Read from the call's input. |
| `command` | string | The shell command a call ran. | Shell tools only. |
| `pattern` | string | A search tool's pattern. | Search tools only (`grep`, `glob`, ...). |
| `tool_input_json` | text | The call's whole input, as JSON. |  |
| `orchestration_kind` | string | `subagent_spawn` on a call that starts another helper agent. | Empty otherwise (48 rows set). |
| `category` | string | The tool call's kind, by SWE-chat's rules: `Action`, `Research`, `Orchestration`, `Other`. | A tool's output carries its call's. |
| `bash_category` | string | A shell command's kind: `file ops`, `git`, `package manager`, `test/build`, `other`. | Empty where no command was recorded. |
| `agent` | string | The coding agent. | Claude Code or Copilot CLI in this version. |
| `strategy` | string | How Entire saved the parent session. |  |
| `language` | string | The prompt's language, by SWE-chat: `ENGLISH`, `CHINESE`, ... | Empty except on 46 helper prompts that match a prompt SWE-chat labelled. |
| `prompt_intent` | string | SWE-chat's label of what a prompt asks: `understand`, `debug`, `create new code`, `refactor`, `test`, `git`, `connect`, `other`. | Written by a model, for SWE-chat. Empty except on 44 helper prompts that match a prompt SWE-chat labelled. |
| `prompt_pushback` | string | SWE-chat's pushback label: `correction`, `rejection`, `failure_report`, `non_pushback`. | Written by a model, for SWE-chat. Empty except on 44 helper prompts that match a prompt SWE-chat labelled. |
| `source` | string | `entire` or `swe-chat-v2`: the sources whose sessions record helper agents. | Ours. |
| `session_key` | string | The parent session's `<source>:<source_id>`. | Ours. Links the row to the session tables and to the parent's rows in `conversations`. |
| `typed_by_developer` | bool | Always `false`: nobody types to a helper agent. | Ours. Its prompt is written by the parent agent. |
| `actor` | string | Who made the row: `agent` (the helper, or the parent agent for the helper's prompt), `tool` or `harness`. | Ours. |
| `kind` | string | What the row is. Here: `text`, `thinking`, `tool_call`, `tool_result`, `progress`, `notice`. | Ours. Finer than `turn_type`. |
| `is_error` | bool | The tool's output was marked as a failure. | Ours. Set on tool outputs where the format records it; empty elsewhere. |
| `parent_uuid` | string | The log entry this one follows from, as the format records it. | Ours. |
| `content_truncated` | bool | `true` if a tool's output was cut at 100,000 characters. | Ours. Set on tool outputs only; empty elsewhere. The whole output is in the session's `files`. |
| `raw_file` | string | The file in the parent session's `files` the row came from: the helper's own file, or the main transcript where the helper's entries sit there. | Ours. |
| `raw_line` | int | The row's line in that file. | Ours. |

Other tables: [session tables](sessions.md), [`conversations`](conversations.md). The [README](../../README.md) says where the data is and how to get it.

# `conversations` columns

`conversations/<source>/*.parquet`: one row per piece of a message, in order: a prompt, a reply's text, a thinking step, a tool call, a tool's output, a notice. Rows of one session share `session_key`, and `turn_number` gives their order.

The first 38 columns are SWE-chat v2's `conversations` columns without its `user_id` (39 there; checked against its own file on 2026-11-09), so code written for SWE-chat that reads columns by name runs on this table unless it reads `user_id`. One difference in order: `orchestration_kind` comes before `category` here and after `bash_category` there. The last 12 are ours.

"Empty" means null. Types are Parquet's: `string`, `text` (a string that can be long), `int`, `bool` and `timestamp` (UTC).

| Column | Type | Description | Notes |
|---|---|---|---|
| `turn_id` | string | `<session_id>#<turn_number>`, or `<session_key>#<turn_number>` where the agent records no session id. |  |
| `session_id` | string | The agent's own session id. | Empty for Aider and OpenHands feedback, and for about half of SpecStory's sessions. |
| `checkpoint_pk` | string | Entire's `<repository>#<checkpoint id>` the files were read from. | Entire and SWE-chat v2 only. |
| `repo_id` | string | The GitHub repository, as `owner/name`. | Empty for DataClaw and OpenHands, which record none. |
| `turn_number` | int | The row's position in its session, from 0. | With `session_key`, it identifies the row. |
| `conversation_turn_number` | int | The position among the dialogue rows only (prompts and replies), from 0. | Empty on other rows. |
| `source_entry_uuid` | string | The id of the log entry the row came from. | Where the format records one. |
| `response_id` | string | The id of the model's API response. | Where the format records one. |
| `content_block_index` | int | Which piece of its message the row is, from 0. |  |
| `role` | string | SWE-chat's role: `user`, `assistant`, `tool_use`, `tool_result`, `metadata`. | Who wrote the row is in `actor`. |
| `turn_type` | string | SWE-chat's row type: `user_prompt`, `assistant_response`, `assistant_thinking`, `tool_use`, `tool_result`, `system_injected`, `system_event`, `queue_operation`, `progress`, `file_snapshot`, `summary`. |  |
| `is_conversational` | bool | `true` for prompts and replies (`user_prompt`, `assistant_response`). |  |
| `content` | text | The row's text, with secrets as `[REDACTED]`. Images and documents appear as `[Image: image/png]`. | A tool's output over 100,000 characters is cut; see `content_truncated`. |
| `model` | string | The model that wrote the row. | Agent rows. |
| `timestamp` | timestamp | When the log entry was written. | Empty where the format records no time. |
| `input_tokens` | int | The model call's input tokens. | Once per call, on its first text row (else its first tool call). Anthropic models count without cache reads; OpenAI's (Codex) include cached input. |
| `output_tokens` | int | The model call's output tokens. | Placed as `input_tokens`. |
| `cache_creation_input_tokens` | int | Tokens the call wrote to the prompt cache. | Placed as `input_tokens`. |
| `cache_read_input_tokens` | int | Tokens the call read from the prompt cache. | Placed as `input_tokens`. |
| `is_continuation` | bool | `true` on a user row opening with Claude Code's "This session is being continued..." summary. |  |
| `is_first_turn` | bool | `true` on the session's first dialogue row. |  |
| `word_count` | int | Words in `content`, split on whitespace. |  |
| `char_count` | int | Characters in `content`. |  |
| `tool_name` | string | The tool called, as the agent names it. | A tool's output carries its call's. |
| `tool_call_id` | string | The call's id. | A tool's output carries its call's, which pairs the two. |
| `file_path` | string | The file a tool call works on. | Read from the call's input. |
| `command` | string | The shell command a call ran. | Shell tools only. |
| `pattern` | string | A search tool's pattern. | Search tools only (`grep`, `glob`, ...). |
| `tool_input_json` | text | The call's whole input, as JSON. |  |
| `orchestration_kind` | string | `subagent_spawn` on a call that starts a helper agent. | Empty otherwise. |
| `category` | string | The tool call's kind, by SWE-chat's rules: `Action`, `Research`, `Orchestration`, `Other`. | A tool's output carries its call's. |
| `bash_category` | string | A shell command's kind: `file ops`, `git`, `package manager`, `test/build`, `other`. | Empty where no command was recorded. |
| `queue_op_subtype` | string | For a message queued while the agent worked: `user_prompt_enqueued`, `user_prompt_delivered`, `user_prompt_discarded`, `task_notification`, `other`. | Claude Code's queue rows only. |
| `agent` | string | The coding agent. | Empty where the source does not name it. |
| `strategy` | string | How Entire saved the session: `manual-commit`, `auto-commit`, `import`, ... | Entire and SWE-chat v2 only. |
| `language` | string | The prompt's language, by SWE-chat: `ENGLISH`, `CHINESE`, ... | Only on sessions SWE-chat v2 holds, in the `swe-chat-v2` and `entire` folders. |
| `prompt_intent` | string | SWE-chat's label of what a prompt asks: `understand`, `debug`, `create new code`, `refactor`, `test`, `git`, `connect`, `other`. | Written by a model, for SWE-chat. Only on sessions SWE-chat v2 holds, in the `swe-chat-v2` and `entire` folders. |
| `prompt_pushback` | string | SWE-chat's pushback label: `correction`, `rejection`, `failure_report`, `non_pushback`. | Written by a model that read the whole session, for SWE-chat. Only on sessions SWE-chat v2 holds, in the `swe-chat-v2` and `entire` folders. |
| `source` | string | `entire`, `specstory`, `aider`, `swe-chat-v2`, `dataclaw` or `openhands-feedback`. | Ours. |
| `session_key` | string | `<source>:<source_id>`. It links the row to the session tables. | Ours. |
| `typed_by_developer` | bool | `true` if a person may have typed this message. | Ours. Every message a developer wrote is marked; where the format records nothing (SpecStory, Aider, DataClaw, unattended runs such as `codex exec` or `claude -p`), a message no rule names is taken as theirs, so some a program wrote are marked too. A turn SpecStory's export keeps without its content (pasted output, an image) is marked with no text (1,690 rows in version 2026-11-02's kept sessions). |
| `actor` | string | Who made the row: `developer`, `agent`, `tool`, `harness` (the agent program's own text), `peer` (another agent or a teammate, including a brief one agent wrote for a helper agent that the helper's own session files as its first user message). | Ours. |
| `kind` | string | What the row is: `text`, `thinking`, `tool_call`, `tool_result`, `notice`, `command`, `shell`, `queued`, `compaction`, `interrupt`, `rejection`, `plan`, `progress`, `snapshot`. | Ours. Finer than `turn_type`. |
| `is_error` | bool | A tool's output, or a status notice, marked as a failure. | Ours. Set on tool outputs where the format records it, and on 401 run-status notices of a few agents' runners (`system_event`); empty elsewhere. |
| `copy_of` | int | On the delivered copy of a queued message, the `turn_number` where it was queued; -1 on a developer's message a forked helper thread (Codex) inherited from its parent's conversation. | Ours. Skip these rows to count each message once. |
| `parent_uuid` | string | The log entry this one follows from, as the format records it. | Ours. |
| `content_truncated` | bool | `true` if a tool's output was cut at 100,000 characters. | Ours. Set on tool outputs only; empty elsewhere. The whole output is in the session's `files`. |
| `raw_file` | string | The file in the session's `files` the row came from. | Ours. |
| `raw_line` | int | The row's line in that file. | Ours. |
| `pushback` | bool | `true` if a typed message pushes back on the agent's work: corrects or redirects it, rejects or reverts it, or reports it broken. | Ours. Written by gpt-6-luna from the message alone. Typed rows only; in version 2026-11-02, 1,766 have no label (copies included): 1,690 with no text to read, and 76 the model gave no label for. |

Other tables: [session tables](sessions.md), [`conversations_subagents`](conversations_subagents.md). The [README](../../README.md) says where the data is and how to get it.

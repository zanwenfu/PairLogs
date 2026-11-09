# Labels beside the tiers

Tables written beside the release tiers (`collect tiers`, `collect/tier_labels.py`), so that every label ships with
the dataset. None of them holds text. "Empty" means null. Types are Parquet's. Paths are as in the published download:
tier A at the folder's root, tier B under `tier-b/` (`collect tiers` writes the two as `a/` and `b/`).

## Tier B: message labels

`tier-b/labels/conversations/<source>/*.parquet` and `tier-b/labels/conversations_subagents/<source>/*.parquet`: one
row for each row of tier B's sessions in `conversations` and `conversations_subagents`, with the same file names as the
version's own files. Tier B ships pointers, not text; these rows carry our labels for those sessions. A session rebuilt
from its source (`collect update --only-file repositories.txt`) can take the pushback labels by `text_id`, as
`collect.pushback.apply` does. Join by `text_id` rather than by `turn_number`, because a later build can number rows
differently.

| Column | Type | Description | Notes |
|---|---|---|---|
| `session_key` | string | The session, as in `pointers.parquet`. |  |
| `task_pk` | string | The helper agent's task. | `conversations_subagents` only; with `session_key` and `turn_number` it identifies the row. |
| `turn_number` | int | The row's position in its session (or its helper's rows), from 0. |  |
| `typed_by_developer` | bool | `true` if a person may have typed this message. | As in `conversations`. |
| `actor` | string | Who made the row: `developer`, `agent`, `tool`, `harness`, `peer`. | As in `conversations`. |
| `kind` | string | What the row is: `text`, `thinking`, `tool_call`, `tool_result`, ... | As in `conversations`. |
| `is_error` | bool | A tool's output, or a status notice, marked as a failure. | As in `conversations`. |
| `copy_of` | int | On the delivered copy of a queued message, the `turn_number` where it was queued. | `conversations` only. |
| `pushback` | bool | Our pushback label (gpt-6-luna, from the typed message alone). | `conversations` only; typed rows only. |
| `text_id` | string | The first 24 hex characters of the SHA-256 of the row's content as published. | Typed rows only; empty on every other row. |

SWE-chat v2's own labels (`prompt_intent`, `prompt_pushback`, `language`) are not repeated here. SWE-chat's release
holds them.

## Tier B: who wrote SWE-chat v2's labelled prompts

`tier-b/labels/swe_chat_prompts.parquet`: one row for each of the 201,485 user prompts SWE-chat v2 labels. Each row says
who wrote the prompt, as matched to our rows of the same session by `scripts/paper_e1/swe_prompts_who_typed.py`. The
matching is m5's: the opening text first, then text contained in a longer message, then rows of any role, then fixed
opening rules.

| Column | Type | Description | Notes |
|---|---|---|---|
| `turn_id` | string | SWE-chat's own `turn_id` (`<session_id>#<turn_number>`). | Unique over its labelled prompts: join on it. |
| `session_id` | string | SWE-chat's `session_id`. |  |
| `turn_number` | int | SWE-chat's `turn_number`. |  |
| `written_by` | string | `typed` (by a person), `action` (a person's action: an interrupt, a plan approval, a command, an attachment), `program` (instruction files, tool output, the harness's text, another agent's message), or `unknown`. |  |
| `pushback` | bool | Our pushback label, where the prompt matched a typed row of ours. | Empty elsewhere. |

## Each tier: repositories and their owners

`repositories.parquet` (tier A) and `tier-b/repositories.parquet`: every GitHub repository the tier holds. In tier A that means
the repositories of its sessions and of the copies kept with them. In tier B it means those of its pointers.

| Column | Type | Description | Notes |
|---|---|---|---|
| `repo` | string | The repository, as `owner/name`. |  |
| `owner_type` | string | The owner's account on GitHub: `person`, `organisation`, or `not found` (gone from GitHub when looked up). | From GitHub's public profiles, read 2026-11-02. |

Account age, the number of public repositories and university profiles appear only as totals, by owner type, under
`labels.owners` in the tier's `changelog.json`. They are never given per owner.

## Tier B: where else each session was found

`tier-b/pointers.parquet` holds `also_in`: every other place the session was found, as a list of
`{source, source_id, found_url}`, like `also_in` in the session tables. Some kept sessions have another part saved
under the same session id elsewhere: in version 2026-11-02, about 0.9% of kept sessions, often in another repository.
Such a part is listed here even though its own text is not kept. `changelog.json` counts the pointers that list one
(`counts.also_in`).

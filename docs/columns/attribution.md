# Lines the agent wrote (Entire's own attribution)

`attribution/entire.parquet` in each tier (tier A at the download's root, tier B under `tier-b/`): for every kept
Entire session whose checkpoint `metadata.json` holds `initial_attribution`, Entire's own count, made when the
checkpoint was committed, of the committed lines the agent wrote and those a person added, changed or removed. Read
from the session's `metadata.json` as published (`scripts/release/attribution_release.py`); numbers only, no text.
Tier A 9,821 sessions, tier B 12,368. Of the 20,404 that committed lines, the agent wrote 99% or more in 41.6%, none
in 22.3%, and some in 36.0% (SWE-chat v2, from the same Entire field, reports 41.1%, 25.0% and 33.9% on its own
sessions).

Entire computes these numbers, not this pipeline: they count lines in the commit the checkpoint was saved with, not
code that survives later, and a session whose work was never committed has none. `metric_version` and the two
columns it brought (`agent_removed`, `total_lines_changed`) are missing in older checkpoints.

| Column | Type | What it holds |
|---|---|---|
| `session_key` | string | the session, as in the session tables' `session_key` |
| `checkpoint_id` | string | Entire's checkpoint the numbers belong to |
| `agent` | string | the session's agent, as in the session tables |
| `calculated_at` | string | when Entire computed the numbers (ISO time) |
| `agent_lines` | int | committed lines the agent wrote |
| `agent_removed` | int | lines the agent removed (newer checkpoints) |
| `human_added` | int | committed lines a person added |
| `human_modified` | int | committed lines a person changed |
| `human_removed` | int | lines a person removed |
| `total_committed` | int | lines in the commit |
| `total_lines_changed` | int | lines changed in all (newer checkpoints) |
| `agent_percentage` | float | the agent's share of the committed lines, 0 to 100, as Entire gives it |
| `metric_version` | int | Entire's version of this count (newer checkpoints) |

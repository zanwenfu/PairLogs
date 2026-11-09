# Labels about the work

`work_labels/` in each tier (tier A at the download's root, tier B under `tier-b/`): each repository's kind of project
and domain, and each session's kind of task, with the kind of project and domain it inherits. Labelled by a model,
Claude Haiku 4.5, from the written guide in [docs/work-labels-guide.md](../work-labels-guide.md); `meta.json` says how
(model, settings, guide version and digest, what each call read, the date). The label names are the guide's lists.
None of these tables holds text. Use their shares, not single labels: a second run through a stricter answer form
agreed with this run on 82% of labels on 100 items (task 88%, project 81%, domain 77%), a model against a model, not
a check of validity. A person labelling the same 100 items gave the model's main label on 65% of tasks, 65% of
projects and 58% of domains (kappa 0.57, 0.55, 0.47), and one of its labels (main or other) on 84%, 77% and 77%.

## repositories.parquet

One row per repository the tier holds sessions of (tier A 970, tier B 2,471).

| Column | Type | What it holds |
|---|---|---|
| `repo` | string | `owner/name`, as in the session tables' `repo` |
| `project` | string | the main kind of project (for example product, library or tool, research code, coursework or learning) |
| `project_also` | list of string | up to two other kinds that also fit |
| `domain` | string | the main domain |
| `domain_also` | list of string | up to two other domains that also fit |

## sessions.parquet

One row per kept session with a typed message (tier A 18,922, tier B 25,568).

| Column | Type | What it holds |
|---|---|---|
| `session_key` | string | the session, as in the session tables' `session_key` |
| `source` | string | the source, as in the session tables |
| `task` | string | the main kind of task the developer asked for (for example understanding code, bug fix, new feature); empty for 5 sessions the model gave none |
| `task_also` | list of string | up to two other kinds of task in the session |
| `project`, `project_also`, `domain`, `domain_also` | as above | the session's repository's labels, or the session's own where it names no repository or GitHub no longer has it |
| `project_from` | string | `repository` or `the session`: where `project` and `domain` came from |

## meta.json

How the labels were made: the labeller and its settings, the guide's version and digest, what each call read, the
date, and how answers off the guide's lists were asked again.

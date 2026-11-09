# Design (v0)

Approved on 2026-10-03. Numbers marked *measured* were measured that day on
small samples; the published card recounts every number from the stored rows.

## What it is for

A fresh, general collection of real coding-agent sessions for a research group
whose needs vary: one place, raw files as their sources served them, labels
that state facts and nothing else. Others add their own labels.

## Sources in v0

Six sources: real coding-agent sessions, kept as raw transcripts, the most
sessions for the least work. Each is its own subset, so no source's structure
mixes with another's.

| Source | How it is found | How it is fetched | Measured |
|---|---|---|---|
| Entire | GitHub commit search for the `Entire-Checkpoint:` trailer | anonymous git: the `entire/checkpoints/v1` branch or the `refs/entire/checkpoints/<xx>/<id>` refs, latest copy of each session | 95,682 trailer commits; 4 of 14 sampled repos had no checkpoint data on GitHub |
| SpecStory | code search `path:.specstory/history`, sliced by file-name date and size under the 1,000-result cap | contents API; a shallow clone where files exceed search's 384 KB limit | 15,936 files; 14% over 384 KB |
| Aider | code search `filename:.aider.chat.history.md` | contents API; one row per file, kept whole | 5,264 files |
| SWE-chat v2 | copied from Hugging Face at a pinned revision | the dataset's own files | 17,968 sessions, 29.3 GB, ODC-BY |
| DataClaw | Hugging Face datasets tagged `dataclaw` | each dataset's own files, pinned revisions | 49 datasets, 13.4 GB, MIT |
| OpenHands feedback | copied from Hugging Face at a pinned revision | the dataset's own file | 275 sessions, 114 MB, MIT, shared by their authors for redistribution |

Deferred, documented in an issue, and not in v0:
- Amp: most threads need a sign-in. Amp ended public sharing on 2026-06-02. Measured on 2026-10-03: 0 of 15 threads linked from commits were readable, and 2 of 15 linked from issues and PRs;
- OpenCode share links: its Terms of Use forbid crawling and copying shared content, and its robots.txt disallows the share pages. OpenCode sessions that Entire records are in the Entire subset;
- Pi share links (about 450, in secret gists);
- Cursor exports (3,640 files, text only, no model names);
- Copilot agent logs (not viewable signed out);
- AIDev (pull requests, not sessions);
- DevGPT (ChatGPT chats from 2023);
- Hugging Face datasets tagged agent-traces (503 datasets: real and synthetic mixed, 373 with no licence).

Out of scope, said in the card:
- TraceLab (no message text);
- Claude Code on the web, Codex cloud and Devin (viewing needs a login);
- Cursor and Windsurf shares (team-only);
- the Codex, Gemini and Copilot CLIs, Zed and Continue used without a recorder (sessions stay local; those that Entire records are in the Entire subset);
- Cline, Roo and Kilo exports and git-note tools (almost nothing public).

## One row per session

Scraped sources give one row per session; for Aider, one row per file. Each row
carries the raw files exactly as fetched, after redaction, as a list of
`{path, content, bytes, sha256}`, plus these labels:

| Labels | What they say |
|---|---|
| `source`, `source_id`, `found_url`, `found_commit`, `fetched_at`, `collector_version`, `content_sha256` | where and when it was found; for Entire, `found_commit` is the commit the session produced |
| `repo`, `repo_url`, `repo_license`, `repo_language` | the repository it was found in. The licence is GitHub's SPDX id, `none` when the repository has no licence, or `unrecognized` when GitHub cannot identify it. |
| `agent`, `agent_version`, `models` | as the files record them |
| `started_at`, `ended_at` | as the files record them, if they do |
| `n_user_messages`, `n_agent_messages`, `n_tool_calls`, `has_tool_results`, `has_timestamps` | counts from a cheap parse, defined per format below; null where a format gives none |
| `truncated`, `truncated_reason`, `bytes` | whether the files are whole |
| `dedup_key`, `duplicate_of`, `also_in` | see Deduplication |
| `redactions` | counts by kind: secrets per rule, emails, home-path usernames |

Counts are defined per format, and the card states each definition:
- **Claude Code JSONL:**
  - `n_user_messages` counts what the developer typed. Claude Code marks who wrote an entry with `origin.kind`, `turnOrigin` and `promptSource`. A meta entry counts only when it expands a command the developer ran.
  - Not counted as user messages: tool results, Claude Code's own notices, hook output, compaction summaries, accepted-plan rows, and other agents' messages.
  - `n_agent_messages` excludes Claude Code's own `<synthetic>` replies.
- **Other formats:** defined after reading samples. Where a format cannot tell typed text from inserted text, the field is named for what it does count (`n_user_entries`, say) and the card says so.

Not in v0:
- anything a model writes (pushback, quality, task type);
- real-versus-synthetic judgements;
- a shared conversation format.

## Copied datasets

- **What is copied:** their files, as they are, in their own subset, at a recorded revision.
- **Sources table:** one row per copied dataset, giving name, link, revision, licence, attribution, rows, and whether it was copied or linked (with the reason). It also gives `provenance_quote`: what the dataset's own card says about where its data came from, quoted and not judged.
- **Secrets:** only a secret's span is redacted. The card says "copied from `<source>` at revision `<r>`; N secret spans redacted, nothing else changed", as CC-BY and ODC-BY require.
- **No-derivatives licences:** a dataset under one is linked, not copied.
- **SWE-chat v2:** gated. Its gate is accepted by the dataset's owner on Hugging Face, and its terms are read for re-sharing before copying. ODC-BY requires conveying it under ODC-BY with its notices.

## Deduplication

Exact matches only, within and across sources.

- **Key:** the agent's own session id, namespaced by kind so short ids cannot collide: `claude-code:<uuid>`, `codex:<uuid>`, `amp:<thread>`, and so on. Otherwise `sha256:<hex>` of the raw content. Keys are computed from the unredacted files, before the scan.
- **When:** at the end of every update, over all sources, old rows and new together.
- **Nothing is deleted.**
  - One copy is kept: the most complete, meaning the most content, with tool results. A tie goes to source priority.
  - Every other copy keeps its row, with `duplicate_of` naming the kept row, and the kept row's `also_in` lists every place the session appears. Content is stored once among scraped rows.
  - Copied datasets stay whole; their matches are recorded in a side table.
- **A session seen again with more turns** is the same session updated, not a new one. Entire's checkpoints grow.
- **Not in v0:** near-duplicates, such as one chat in two formats or a session resumed under a new id.
- **The changelog** reports exact-match counts for each pair of sources.

## Updates and versions

- **State:** a ledger per source keeps the ids seen, content hashes and discovery cursors. A stopped run resumes, and only what is new or changed is fetched.
- **The ledger is rebuildable:** the published table holds every key, hash and found URL, so the ledger can be rebuilt from it plus a fresh discovery. A copy is also kept off the run machine with each run.
- **Command:** `collect update [--source NAME ...] [--dry-run] [--publish]`, as in the README. A run that finds nothing new prints `nothing new: no version published` and exits 0.
- **Versions:** one dated tag per published run, with a changelog. It gives new and total counts per source, redactions per rule, and duplicate counts per pair of sources.
- **The first publish waits for the owner's approval.**

## Concurrency and limits

- **Sources run in parallel; within each, fetches run in parallel** up to that service's limit.
- **One shared GitHub limiter,** since every source uses one token. It keeps under:
  - 10 code searches a minute;
  - 30 commit or issue searches a minute;
  - 5,000 other requests an hour;
  - a few requests in flight, for GitHub's secondary limits.
- **Anonymous git and other hosts:** in parallel, polite per host.
- **Hugging Face:** under its download limits.
- **CPU work** (parsing, hashing, the scan) is spread over all cores.

## Safety

- **Scanned and redacted:**
  - gitleaks runs over every file before anything is published, copied files included;
  - findings become `[REDACTED]`, and so do emails and the usernames inside home paths.
- **Calibrated first:**
  - the scanner runs on a sample from each source, and every finding is read before the rule set is chosen;
  - its generic high-entropy rules fire on commit hashes, UUIDs, base64 blobs and lockfile digests, and redacting those would damage the content;
  - label fields are never redacted.
- **Checked:**
  - per source, the files scanned must equal the files published;
  - the card says what was redacted, with counts.
- **Unredacted files** stay on the run machine only.

## Licences

- **Scraped rows:** each carries its repository's licence. Repositories with no licence are included and labelled, so they can be filtered out.
  - GitHub's terms let public content be used, displayed and forked through GitHub.
  - Its acceptable-use policy allows research on public, non-personal information when the resulting publications are open access.
  - Measured in the samples: no licence on 10 of 32 SpecStory repos, 9 of 40 Aider repos and 1 of 14 Entire repos.
- **Copied datasets:** each keeps its own licence and attribution.
- **The dataset:** gated with manual approval, under research-only terms.

## Operation

- **Where it runs:** everything runs on one cloud VM, with a data disk if the collection outgrows its own.
- **The laptop script:**
  - syncs the code;
  - starts the VM;
  - runs `collect update` under a busy marker;
  - deallocates the VM when the run ends and no other marker remains.
- **The repository** holds no machine names, addresses or account identifiers.

## Order of work

1. Entire end to end on a small sample, with its rows and a card draft reviewed.
2. The other five sources.
3. The scanner calibrated on samples.
4. The full run.
5. The scan report, every card number recounted from the stored rows, a review, the owner's approval, the first publish.

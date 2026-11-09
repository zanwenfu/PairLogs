# Datasheet: PairLogs, version 2026-11-02

The questions of "Datasheets for Datasets" (Gebru et al., 2021), answered for one version. Every count is from the
version's stored rows; the README says how to run and read the data, and [docs/columns](columns/) defines each
column.

## Motivation

**Why was it made?** Real sessions between developers and AI coding agents are public in many places (agent logs
committed to GitHub, datasets on Hugging Face), each in its own format. And each log files more under "user" than
what a person typed: instruction files, tool output, other agents' messages, prompts that scripts send. PairLogs
gathers these sessions into one schema, removes duplicates across sources, masks secrets and names, and marks who
wrote each message, so a study can count what developers typed.

**Who made it, and who paid?** The maintainer named in the README. No outside party asked for it. The model labels
were paid from cloud credits.

## Composition

**What is one instance?** A session: one conversation between a developer and a coding agent (for Aider and
SpecStory, one chat-history file). Each session has one row in its source's session table (36 columns) and one row
per message, tool call or tool result in `conversations`, and in `conversations_subagents` for the agent's helpers
(50 columns each).

**How many?** 61,609 distinct sessions, and 33,595 copies of them found in other places, kept as rows that point
to the kept session (`duplicate_of`).

| Source | Sessions | Tier A (full text) | Tier B (pointers) | With a typed message |
|---|---|---|---|---|
| Entire (logs in GitHub repositories) | 28,234 | 12,232 | 16,002 | 22,632 |
| SpecStory (chat histories in GitHub repositories) | 15,110 | 4,495 | 10,615 | 14,379 |
| DataClaw (developers' own uploads to Hugging Face) | 8,358 | 8,358 | | 4,445 |
| Aider (chat histories in GitHub repositories) | 5,948 | 238 | 5,710 | 349 |
| SWE-chat v2 (a published dataset) | 3,730 | | 3,730 | 2,523 |
| OpenHands feedback (a published dataset) | 229 | 229 | | 227 |
| All | 61,609 | 25,552 | 36,057 | 44,555 |

The sessions come from 3,602 GitHub repositories of 2,733 owners (DataClaw and OpenHands sessions name no
repository). The files name 33 agents (some are one product under several names). The largest are Claude Code (25,171 sessions), Codex (9,450, with its scoped exports), Aider (5,948),
Cursor (4,156, with Cursor CLI) and OpenCode (3,282); 8,477 sessions do not name their agent. The first session began in November
2023 and the last in October 2026; 43,184 began in 2026. 11,835 record no start time, among them every Aider session.

Tier A holds 9.8 million message rows (and 104,583 helper rows). By who made them: the agent 4.93 million, tools
2.73 million, the agent program's own text 1.91 million, the developer 206,612, another agent 10,203.

**Is it a sample?** It is what GitHub's search and Hugging Face's listings found, not every public session. For
Entire, the one source whose coverage was estimated, a two-sample estimate puts the public repositories holding
sessions at about 1,950 (1,877 to 2,027), of which 1,658 are in the data: at most about 85%, since both captures lean
on GitHub's search, so the estimate is likely low. Our own searches alone find 1,535 (79%). The sessions are not a
sample of developers: they come from people who chose to publish their logs, and a few repositories hold many
sessions (one Aider repository holds 5,356 benchmark runs). Count by repository, and use intervals that resample
repositories.

**What does each instance hold?** The session's raw files, masked (in the session table's `files`; for DataClaw
and OpenHands, in the dataset's own files under `copied/`), and its messages parsed into rows. The labels:

| Label | Made by | What it says |
|---|---|---|
| `typed_by_developer`, `actor`, `kind` | the pipeline's fixed rules and each agent's own records | whether a person may have typed a message; who made each row; what the row is |
| `pushback` | a model (gpt-6-luna), reading each typed message alone | whether the message pushes back on the agent's work: 141,781 of the version's 467,782 typed rows (copies included) |
| `work_labels/` | a model (Claude Haiku 4.5), from a written guide | each session's kind of task, and its project's kind and domain, for sessions with a typed message |
| `attribution/entire.parquet` | Entire itself, at commit time (read from each session's `metadata.json`) | the committed lines the agent wrote and those a person added, changed or removed, for 22,189 kept Entire sessions |
| `duplicate_of`, `also_in` | the pipeline | where else a session was found |
| `repositories.parquet` `owner_type` | GitHub's public profiles | whether each repository's owner is a person or an organisation (account ages and public-repository counts appear only as totals, in each tier's changelog) |
| `labels/swe_chat_prompts.parquet` | the pipeline, matched to SWE-chat v2's rows | who wrote each of SWE-chat's 201,485 labelled prompts (typed 118,234; a program 68,977; a person's action 4,775; unknown 9,499), keyed by SWE-chat's own `turn_id` |

**Is anything missing?** A session deleted from its source before it was fetched is not in the data. Aider records
no times. Where a format records no author (SpecStory, Aider, DataClaw), the typed mark cannot read one and errs
toward marking typed. 1,766 typed rows (copies included) have no pushback label: 1,690 hold no text, and the model gave
no label for the other 76.

**Are there errors or noise?** The typed mark misses prompts that a program sent into fewer than 10 sessions. A
person (the maintainer) labelled 184 entries filed as the user's without seeing the mark: where both decided, they
agree on 64 of SWE-chat v2's 75 labelled prompts (85%) and 66 of our 84 (79%; 71 of 84 after this version's fix of what
the check found, on the same entries, so not an independent measure). A second person, marking the same 184 entries
on their own page (the same cards and guide, on 2026-11-09), agrees with the first on 174 of the 180 both decided
(96.7%, kappa 0.93; 97 of 99 of SWE-chat's prompts, 77 of 81 of ours); against the mark they agree on 60 of 74 of
SWE-chat's prompts and 63 of 81 of ours. The model labels are a model's reading of short texts: use their shares, not single labels. About 560 kept
sessions (0.9%) miss a part of their work that another file under the same session id holds (often in another
repository): that file is kept as a copy, not as text of its own, and the kept session's `also_in` says where it is
(see Duplicates below).

**Is it self-contained?** Tier A is. Tier B's sessions are rebuilt by the pipeline from GitHub (a session deleted
there is not rebuilt), and SWE-chat v2's from SWE-chat's own release, under its terms.

**Could it identify people, or hold sensitive or offensive content?** The sessions were public, but they hold
developers' words and code. The `repo` column names GitHub accounts. Developers' messages can be rude or
frustrated. Code and logs can hold internal host names and project details. The masking, below, covers most
secrets, emails, home-folder names and people's full names, not all.

## Collection

**How was it found and fetched?**
- Entire: GitHub's commit search for the trailer Entire writes (`Entire-Checkpoint:`), a code search for Entire's
  settings file, and the repositories SWE-chat names. Of the 1,658 repositories with kept sessions, the commit search
  found 1,511, SWE-chat's list added 123 and the settings search 24.
- SpecStory and Aider: GitHub code search for their history files.
- DataClaw, OpenHands feedback and SWE-chat v2: copied from Hugging Face at a pinned revision. DataClaw is 49
  uploads; 19 are byte-for-byte copies of another and are left out. The changelog counts each copied upload's lines
  (`rows`, 25,019 for DataClaw's 30), not its sessions: they were read as 15,340 sessions (one per record line, and
  one per raw Claude Code log, whose 17 files hold 8,898 lines), leaving out 766 lines that are not JSON and 32 lines
  of a file in no known format. 6,982 of the 15,340 repeat another and 8,358 are kept.
- Repositories were read with anonymous git and GitHub's API, under GitHub's rate limits. Each session records when
  it was fetched (`fetched_at`) and from which commit.

**Who collected it?** A program, run by the maintainer. No crowd workers were paid.

**When?** The GitHub sources were fetched on October 25 and 26, 2026; the copied datasets are at the revisions their
credits name. This version was built on November 2 and 3, 2026. The sessions began between November 2023 and
October 2026.

**Ethics review and consent.** No ethics board review was requested: every session was public before we fetched
it, and nothing was asked of any person. The developers were not asked one by one. They committed their logs to
public repositories or uploaded them themselves; OpenHands feedback holds only feedback its users chose to make
public, and they could opt out before its release (its dataset card). Tier A shares
text only from repositories with a recognised licence and from the MIT-licensed datasets; tier B shares pointers,
never text. Anyone can have a session or repository removed (see Maintenance).

## Processing and labels

- **Parsing:** one reader per log format (Claude Code, Codex, Cursor, Gemini CLI, Copilot, OpenCode, Pi, Aider,
  SpecStory and others) writes the same columns.
- **Duplicates:** a session found in several places, or saved twice, is kept once (the fullest copy). Near-copies
  (one conversation saved as several growing snapshots) are marked too. Checked by reading each copy and its kept
  session again from their own files, not from the matching. Of 150 pairs drawn at random, the copy was identical
  to its kept session, or an opening of it: Entire over SWE-chat's copy 25 of 25; Entire over another Entire copy 25
  of 25; DataClaw 7 of 20 (the other 13 are one session exported twice with its text rewritten, the agent's replies
  shared); SpecStory 8 of 14 read (15 drawn: 5 render a tool call differently, 1 is a session's part after a
  compaction, 1 has no rows and is not counted as read); the rest 5 of 15 (10 are the known miss below); near-copies, the same typed messages in order, 48 of 50.
  Of the 31,954 exact copies that share their kept session's key, 98.4% (weighted by stratum) keep at least 90% of
  their typed messages and agent replies in the kept session; SpecStory's copies 79%, and SWE-chat's copies kept
  over Entire's 22% (the miss below). The known miss:
  where one session id holds different parts in different files (an Entire copy whose SWE-chat copy was kept, 220 of
  285; SpecStory files of one chat, 219 of 1,024), only one part is kept as text, about 0.9% of kept sessions. 328
  kept sessions (0.5%) repeat another's whole conversation under ids of their own, mostly one-message starts and
  separate runs of one scripted prompt, kept apart by design.
- **Secrets:** gitleaks 8.30.1 with exceptions calibrated on real transcripts, plus structure rules for private keys
  and known key prefixes. Each secret becomes `[REDACTED]` wherever the session holds it. Tier A as published was
  scanned again (95 GB of text, 0 findings, two planted tokens found). That scan uses the masking's own rules, so it
  shows the masking ran everywhere, not that nothing was missed. So a second, independent scanner (TruffleHog 3.99.8,
  verification off) read tier A too: before masking it flagged 28,206 distinct strings, nearly all ids, hashes and
  pieces of encoded data. Their places were read with the values hidden, and the 325 that could be keys or passwords
  were masked in 2,500 places. Scanned again, the only masked values it still reports are 4 it assembles from separate
  settings, none of them a key.
- **Emails and home-folder names** are masked in the scraped sources. The copied datasets keep their publishers'
  own handling (SWE-chat, DataClaw and OpenHands each describe theirs); in them, we mask secrets, and in tier A
  people's full names.
- **People's full names** in tier A: full names found in typed messages (two or more capitalised words that a
  named-entity model reads as a person with high confidence) are masked wherever they occur in the same session.
  884 names: 23,520 occurrences in 4,070 table rows, and 1,926 in the copied datasets' own files (66 sessions,
  each masked in its own record; the dataset's credit line says so). Of 100 masks drawn at random among names
  found in one session only (on the October 11 version's list, read by an AI reader, not a person), 52 were
  people's names (many of them public: authors, researchers); the rest were phrases or product names. A name that appears only in an agent's or a tool's text is left, by design: in
  tier A, for example, 1,531 Entire tool outputs and 577 DataClaw ones hold a git `Author:` line (174 and 16
  distinct names), commit authors who are public in those repositories' own histories.
  A person read 99 of the name-shaped phrases the masking's own finder sees in 18,000 released tier-A rows (3,000 from
  each kind of writer) and found 42 to be people's names (16 can't tell); tier A still holds a person's name in about
  58,900 places (95% interval 34,200 to 88,700), 87% of them in tools' output such as commit logs (a lower bound: the
  finder sees only full-name shapes). A model (Gemini Argon) given the same cards first found 29 (about 38,400 places);
  where both decided, it agrees with the person on 68 of 83, and every difference is a name the person saw and it
  did not.
- **The typed mark:** from each agent's own records where it keeps them, else from fixed rules. Prompts sent into 10
  or more sessions with the same opening were read, and the scripted ones marked as the harness's. Helper agents'
  sessions filed as sessions of their own hold only their parent agent's messages, and are marked so (`actor` peer):
  an OpenCode session whose file names a parent session; a session whose first typed message is the request another
  session sent to a helper; a DataClaw Claude Code session run only on Claude Haiku, the model Claude Code runs its
  helpers on (DataClaw's uploads hold such helpers without their parents). The person's check found these; on
  2026-11-04, 30 of 30 such DataClaw sessions drawn at random opened with an agent's brief, and of 20 later messages in
  them 19 were the parent agent's and 1 a person's (someone who chose Haiku as the main model), the rule's known cost.
  A helper whose file names no parent and whose request is in no other session is not found. In this version the
  three rules mark 4,329 messages in 1,737 sessions (966 OpenCode helpers, 143 sessions opening with another's
  request, 628 DataClaw Haiku-only sessions), each left with no typed message; of the 6 such briefs the person's check
  found, they mark 5 (the sixth is an OpenCode helper of the kind just named).
- **Model labels:** `pushback` by gpt-6-luna (one call per distinct text, the text alone; the instructions are in
  the changelog). On the 107,636 typed messages SWE-chat v2 also labels, the two agree on 68%: SWE-chat calls 55%
  pushback, ours 29%. SWE-chat's definition is broader (it counts new information and changes of plan), its model
  sees the conversation before the message, and its labels are sampled; ours sees the message alone and gives one
  label per distinct text. Given the end of the agent's last reply as well (up to 1,200 characters), our labeller's
  rate moves from 29% to 27% and its agreement with SWE-chat from 68% to 67% (1,000 typed messages, 250 from each
  cell of agreement): the gap is the definition, not the missing context, as far as that much context shows. A
  person read 80 of these messages, 20 from each cell, each with the end of the agent's last reply before it (up to
  1,200 characters), and labelled them (weighted to the four cells' sizes):
  by our definition (corrects, redirects, rejects or reports broken) the person finds 25% pushback, ours 29%
  (accuracy 0.87) and SWE-chat 55% (0.68); counting new information and changes of plan too, as SWE-chat's definition
  does, the person finds 54%, SWE-chat 55% (accuracy 0.64) and ours 29% (0.66). Each label matches its own
  definition. `work_labels/` by Claude Haiku 4.5 from [the guide](work-labels-guide.md): a second run through a
  stricter answer form agreed with this run on 82% of labels on 100 items (task 88%, project 81%, domain 77%; a model
  against a model, not a check of validity). A person labelled the same 100 items (50 sessions, 50 repositories)
  without seeing the model's labels: the same main label on 65% of tasks, 65% of projects and 58% of domains (Cohen's
  kappa 0.57, 0.55, 0.47); the person's one label is among the model's main and other labels on 84%, 77% and 77% (a
  reading chosen after the first). Most disagreements are between neighbouring labels, such as an AI coding tool as
  "ML or AI" or "developer tools". Use the shares, not single labels.
- **A second reader:** a model (Claude Sonnet 5.5 on Amazon Bedrock, 2026-11-06) read the same cards the person read,
  with the same guide and the same text, never the pipeline's or the person's answer. A reply that did not open with
  one of the answers was asked again, up to three times; 11 of 428 cards got none, and 2 replies that hedged between two
  answers ("A or C?") were stored as the first until 2026-11-07; all 13 count as "can't tell". On who
  typed it, it agrees with the person on 166 of 179 messages where both decided (Cohen's kappa 0.85; SWE-chat's
  prompts 0.92, ours 0.77); on pushback by our definition, 62 of 68 (kappa 0.77); by SWE-chat's broader one, kappa
  0.34. It cost $0.82.
- **A fresh check, weighted to the data:** on 84 of our user entries drawn anew by source and mark (after the helper-
  session fix), the person (the maintainer, on 2026-11-09, marking a fresh page that started empty) puts the typed mark's recall at
  99.3% (95% interval 79.9 to 100) and its precision at 81.6% (63.0 to 90.6). Of the rows the mark calls typed, the
  person reads 17.2% (7.4 to 34.5) as program text (6 cards, all long prompts; two of them every reader calls program
  text); so the program shares measured with the mark are, if anything, low. The model above agrees with the person
  on 89.2% of the cards both decide (kappa 0.76) and puts the precision at 91.8% (73.9 to 96.6) and the recall at 100.0% (81.7 to 100.0); a second model,
  Gemini Argon, given the same cards, at 91.8% (73.3 to 96.7) and 98.0% (80.7 to 98.1). On 80 of SWE-chat's labelled prompts, one per repository (in turn in
  the five largest, which hold 72% of Codex's prompts), programs wrote 16.1% of Claude Code's and 76.2% of Codex's by the model above (Gemini Argon: 15.4% and
  76.2%), where the mark, on the prompts it calls typed or program, says 18.1% and 76.2%. No person read the second draw. On the fresh draw of 84, a second person (2026-11-09, on their own page with the same cards and guide) agrees with the first on 66 of
  the 78 both decided (84.6%, kappa 0.67; the 12 differences: slash commands, shell commands, a hook message, a
  scheduled prompt) and puts the precision at 77.5% (58.9 to 87.8) and the recall at 96.2% (77.9 to 96.8); where logs
  record no author, 25 of 30 rows the mark calls typed are typed by their reading, 24 of 29 where logs do. On 90 more
  rows the mark calls typed where logs record no author (2026-11-09; 30 per source from SpecStory, Aider and DataClaw,
  one per session, none from an earlier draw or a withdrawn repository), both people mark on their own pages, read as
  typed: SpecStory 27 of 30 and 26 of 30, Aider 28 of 29 and 28 of 29, DataClaw 28 of 29 and 27 of 28 (weighted by
  each source's typed rows, 91.8% [83.8, 99.8] and 89.4% [80.4, 98.3]). They agree on 86 of the 87 both decided
  (kappa 0.90). The rest is mostly program text (a
  pasted instruction file, terminal output, a planned file list) and one image notice. On the 80
  pushback messages, the same second person agrees with the first on 61 of 68 both decided (kappa 0.72; SWE-chat's
  broader definition, kappa 0.68), with rates of 22% (ours) and 43% (SWE-chat's), and accuracy 0.82 for this label and
  0.62 for SWE-chat's (by SWE-chat's definition, 0.78 and 0.72).
- **Raw data:** the unmasked files stay with the maintainer and are never published.

## Uses

**Used so far:** to re-measure SWE-chat v2's own pushback label on what people typed, per agent (the paper's use
case; `examples/swechat_gap.py`). In SWE-chat v2's labelled prompts, 34%
were written by programs. On every labelled prompt, SWE-chat's own label finds pushback 27 points less often in
Codex sessions than in Claude Code sessions; on typed prompts only, the gap is about zero.

**Good uses:** how developers direct, correct and interrupt agents; how agents use tools and fail; how sessions
differ across agents, projects and time.

**Care needed:** filter on `typed_by_developer` and `copy_of`; keep one row per distinct session
(`duplicate_of` is null); count by repository; read the model labels as shares.

**Not for:** identifying, profiling or contacting the people in the sessions; claims about all developers.

## Distribution

- **Tier A** (25,552 sessions, the full text, about 20 GB) and **tier B** (36,057 pointers, with the list of
  repositories to rebuild them from) are on Hugging Face, tier A at the top and tier B under `tier-b/`, each table also
  a configuration there; an archive copy with a DOI goes to a non-commercial repository. Tier B carries every label without text:
  each message row's typed mark, actor, kind and pushback label by session and position (with a digest of each typed
  message, so a rebuilt session takes our labels as the build does), the work labels, the owner type, and each
  session's other places (`also_in`). See [docs/columns/labels.md](columns/labels.md).
- **Licences:** each session keeps its source's licence (`repo_license`; DataClaw and OpenHands feedback are MIT).
  What the pipeline adds (the labels, the pointers, the tables' layout and this documentation) is CC BY 4.0.
  SWE-chat v2 (ODC-By, gated by its authors) is not redistributed. Text from repositories with no licence, or one
  GitHub cannot read, is not redistributed. Each tier's `changelog.json` credits every copied dataset it carries.
  The code is MIT.

## Maintenance

- **Who:** the maintainer, through the code repository's issues.
- **Updates:** new versions are built by date from what is new at the sources; each has a changelog counted from its
  rows. Older versions stay available, each under its own version and archive DOI.
- **Removal:** a session, repository or dataset named in a request (an issue, or zanwen.fu@duke.edu) is left out of
  every later version. Before each release every GitHub repository is read again, and a session its repository no
  longer holds (deleted, made private or emptied by its owner) keeps no text: it stays as a pointer, which no longer
  rebuilds (version 2026-11-02: 397 sessions from 18 repositories, read again on 2026-11-06). A published version is
  not changed afterwards. A
  copied dataset is published whole: a session removed from it loses its rows, but its text stays in the dataset's
  own files until the dataset itself is removed.
- **Contributions:** issues and pull requests to the pipeline.

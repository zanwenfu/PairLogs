# The Zenodo records for release 2026-11-02 (what to fill in on zenodo.org)

Two records: the **data** (tier A and tier B, 24 files, 20.97 GB) and the **code** (a clean snapshot of this
repository). The files go up with `scripts/release/zenodo_upload.sh` into a draft; everything below is typed into the
draft's form by the owner, who also chooses public or restricted access and presses publish. Nothing is published by
a script.

## Data record

- **Resource type:** Dataset
- **Title:** PairLogs: developer–agent sessions with message authorship, version 2026-11-02
- **Creators:** as in `CITATION.cff` (the owner confirms the author line)
- **Publication date:** the day it is published
- **Version:** 2026-11-02
- **Licence:** "Other (Open)", because no one licence covers the record. Each session's text keeps the licence of
  its source: its repository's (`repo_license`; tier A holds text only under a recognised open licence), or the
  copied dataset's own (DataClaw and OpenHands feedback MIT). What the pipeline adds (the labels, the pointers, the
  tables' layout and the documentation) is CC BY 4.0. The same sentence is in the README and the datasheet; the
  description says it too.
- **Access:** the owner's choice at publishing. Public; or restricted with a share link (Zenodo's "Share" button:
  anyone with the link can download, no login, so reviewers stay anonymous; the DOI and description stay public),
  which meets MSR's "available at the time of submission ... considered confidential until publication". Not
  "restricted, files on request": a request would show a reviewer's name.
- **Keywords:** coding agents; developer sessions; software engineering; mining software repositories; Claude Code;
  Codex; dataset
- **Related works:** "is supplemented by" the code record's DOI; "is derived from" SWE-chat v2 (Hugging Face
  SALT-NLP/SWE-chat, revision 202b071f), DataClaw's datasets and OpenHands feedback, as each tier's `changelog.json`
  credits them.

**Description** (paste as is):

> PairLogs holds sessions between developers and AI coding agents (Claude Code, Codex, Cursor, Aider,
> OpenCode and others), found in six public sources: logs committed to GitHub by Entire, SpecStory and Aider, and the
> DataClaw, OpenHands feedback and SWE-chat v2 datasets. An open pipeline reads every format into one schema, removes
> copies across sources, masks secrets (and, in the sources it collects itself, emails and home-folder names; in tier
> A, people's full names), marks which messages a person typed, and labels each session's task and project.
>
> Version 2026-11-02 holds 61,609 distinct sessions (53,022 of them from 3,602 GitHub repositories), 44,555 of them
> with a message marked typed by a person; some are scripted or unattended runs. **Tier A** (25,552 sessions) is the full text, from repositories with a recognised open licence and from the
> MIT-licensed datasets: Parquet tables of sessions and messages (36 and 50 columns), the raw files, and the copied
> datasets' own files, masked. **Tier B** (36,057 sessions) is pointers (repository, commit, checkpoint, file hashes)
> and every label without text; the pipeline rebuilds the sessions from their sources
> (`collect update --only-file tier-b/repositories.txt`). SWE-chat v2's own sessions are not redistributed.
>
> Unzip every archive into one folder to get tier A's layout (`croissant.json` describes its tables); `tierB.zip`
> unpacks into `tier-b/`. `SHA256SUMS` lists every file of the record, `MEMBERS.sha256` every file inside the archives.
> `README.md` says how to read the data, `datasheet.md` how it was made and checked, and `columns.zip` defines every
> column. Masking covers most secrets and names, not all: report any you find, and ask for any session or repository
> to be removed, through the code repository's issues or by email to zanwen.fu@duke.edu.

Files to add to the data record beside the archives (from this repository at the release commit): `README.md`,
`datasheet.md`, and `columns.zip` (`docs/columns`), with their checksums added to `SHA256SUMS` and `MD5SUMS`.

## Code record

- **Resource type:** Software
- **Title:** PairLogs pipeline, version 2026-11-02
- **Licence:** MIT
- **Files:** one zip of `scripts/code_snapshot.sh`'s output (committed files at the release commit, no history, no
  paper plan), scanned with gitleaks first.
- **Related works:** "is supplement to" the data record's DOI.

## Order on the day

1. `scripts/release/zenodo_upload.sh --dry-run` (no call to Zenodo).
2. `--only tierA-work_labels.zip` into a new draft; check that the md5 matches and the draft shows the file.
3. `--draft ID` for the rest (about 21 GB from Duke through the laptop; keep it open and on power).
4. Fill in the form above; choose access; publish. Then the code record.
5. Put both DOIs in the paper, `CITATION.cff` and the README.

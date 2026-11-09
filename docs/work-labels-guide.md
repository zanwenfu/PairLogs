# Labels about the work: a guide (DRAFT 2, 2026-10-26, not fixed)

One text for every reader: the model that labels and the person who checks it read this same guide, with the same
definitions and examples. The examples are made up; none comes from a session that will be checked.

The labels say what the work is, never who the person is. No label judges a developer's skill, seniority or
knowledge. Signals about people are observable facts only (decided 2026-10-26): the repository owner's
account type (person or organisation), an organisation's kind where its own profile says it (a company, a university
by an .edu or .ac domain), the account's age and its public repository count. Nothing about a person is inferred by a
model; these signals are reported only in aggregate, never per session or person, and are not part of this guide.

Three labels:

| Label | One per | Read from |
|---|---|---|
| Kind of project | repository (a session with no repository: the session) | name, description, topics, main languages, the README's first 2,000 characters |
| Domain | repository (same) | same |
| Kind of task | session, or one request (a moment) | the developer's typed messages: for a session its first three and up to ten in all; for a moment that one request |

Each answer: one label from the lists below, exactly as written; `also` left empty unless the work is plainly two
kinds (then at most two more); and as evidence a quote of at most 12 words copied from the text shown (not a
summary). Answer "can't tell" when the text shown does not say; never guess from a repository's or person's name.

## 1. Kind of project: what the code is for

| Label | Means | Made-up example |
|---|---|---|
| product | software for users or customers: an app, a service, a startup's or a company's product, a side business | "Taskly: the to-do app for small teams. Sign up at taskly.example" |
| internal system | code an organisation runs for itself: internal tools, back-office services, company infrastructure | "Ops dashboard for the warehouse team; deploys to our k8s cluster" |
| library or tool | a reusable package, framework, command-line tool, plugin or extension for other developers | "fastcsv: a CSV parser for Rust. cargo add fastcsv" |
| agent tooling | building or configuring AI agents: prompts, skills, agent configs, MCP servers, agent frameworks | "A set of Claude Code skills for code review" |
| research code | experiments, models, simulations or analyses in a research project, academic or industrial | "Code for our paper on graph sparsification; run train.py to reproduce Table 2" |
| writing | the repository mainly holds prose: a paper, thesis, book, blog, documentation site or notes | "LaTeX source of my master's thesis" |
| coursework or learning | class assignments, tutorials followed, practice and bootcamp projects | "CS 101 homework 3: linked lists" |
| personal | personal sites, dotfiles, hobby projects and personal automation, not meant for others' use | "My dotfiles"; "a Discord bot for my friends" |
| can't tell | the text shown does not say | an empty README and a generic name |

Rules:
- Skills, prompts, configurations and servers for AI agents are `agent tooling`, even when shared for others to
  install.
- Software people install and use as an app is `product`: a browser extension, a desktop or mobile app, an
  open-source app. Its reusable core, used by other developers' code, is `library or tool`.
- A command-line tool or library whose main use is to reach a company's own app or service is `product`.
- A tool that a club, lab, team or community runs for itself is `internal system`.
- A startup's product is `product`. Research code that ships as a package is `research code` with
  `also: library or tool`. A personal portfolio site is `personal`.
- With no repository, label the project only from what the messages say it is for; code alone, with no word of
  its purpose, is `can't tell`.

## 2. Domain: what area the code is in

`web` (sites, web apps, their back ends), `mobile`, `ML or AI` (training, models, LLM applications), `data` (pipelines,
analytics, databases), `infrastructure` (cloud, DevOps, deployment, networking), `systems` (operating systems,
embedded, hardware, compilers), `games or graphics`, `science` (physics, biology, finance and other domain
computation), `developer tools` (editors, linters, build tools, agent tooling), `writing` (prose, docs), `other`,
`can't tell`. One label, at most two under `also`.

The domain is the area of what the software does for its users. `developer tools` only when its users are developers
building software; a tool for anything else takes that thing's domain (a game engine library: `games or graphics`; a
tutoring app built with AI: `ML or AI` or `web` by what it mainly is).

## 3. Kind of task: what the developer asks the agent to do

| Label | Means | Made-up example request |
|---|---|---|
| new feature | add behaviour, a page, an endpoint, an option | "add a dark mode toggle to settings" |
| bug fix | something is broken or wrong: an error, a failing test, wrong output | "the login page crashes on submit, fix it" |
| refactor | change the structure without changing behaviour: rename, split, clean up, remove dead code | "move the API calls into a service module" |
| tests | write, run or fix tests | "add unit tests for the parser" |
| setup or deploy | environment, dependencies, build, CI, containers, configuration, deployment | "set up GitHub Actions to run the tests" |
| writing | prose: docs, README, a paper, comments, commit or release notes | "write the related work section" |
| experiments or analysis | run experiments, train or evaluate models, analyse data, make plots | "plot accuracy against batch size" |
| understanding | explain code, answer a question, explore or plan without changing code | "how does the cache invalidation work?" |
| review | review code, a diff or a pull request | "review this PR for security issues" |
| routine | git and housekeeping: commit, push, merge, rename files, run a script | "commit and push" |
| can't tell | the messages do not say what is wanted | "continue" |

Rules: a session gets the task its typed messages mostly ask for, with others under `also`. A request that reports a
failure of the agent's own last change ("still broken") is `bug fix`. "Continue", "yes" and option picks take the
task they continue where the messages shown make it plain, else `can't tell`.

## Output

```json
{"label": "bug fix", "also": ["tests"], "evidence": "the login page crashes on submit"}
```

## Open before it is fixed

- The pilot (about 900 sessions) may merge or split labels; this guide is fixed after it, and
  only then shared as final.
- Who checks: about 100 cards labelled by a person (the maintainer), agreement reported per label.

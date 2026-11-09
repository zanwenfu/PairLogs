"""Build the human check of the labels about the work: one local HTML page, no model's answer in it. Each card shows
what the labeller read, and asks for the labels the guide (docs/work-labels-guide.md) defines: a repository card the
kind of project and the domain, a session card the kind of task.

python build_sheet4.py cards.json out.html SAMPLE_ID [--rater NAME --guide docs/work-labels-guide.md]
cards.json: [{"id", "kind": "repo" | "session", "text"}...]
--rater: a second person's sheet: its marks are saved and exported under that name, never mixed with another's.
--guide: the guide's three label sections shown in the page, for a reader without the repository.
"""
import html
import json
import re
import sys

args = sys.argv[1:]
RATER = GUIDE_MD = None
for flag in ("--rater", "--guide"):
    if flag in args:
        i = args.index(flag)
        if flag == "--rater":
            RATER = args[i + 1]
        else:
            GUIDE_MD = args[i + 1]
        del args[i:i + 2]
cards = json.load(open(args[0]))
OUT, SAMPLE = args[1], args[2]
if RATER is not None and not re.fullmatch(r"[a-z0-9-]{1,32}", RATER):
    sys.exit("refused: the rater's name is lower-case letters, digits and dashes")


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)


def guide_html(path: str) -> str:
    """The guide's sections 1 to 3 (the labels, their meanings, made-up examples and rules) as HTML: headings,
    tables, bullets and paragraphs, the only forms they use."""
    text = open(path, encoding="utf-8").read()
    parts = re.split(r"(?m)^## ", text)
    keep = [p for p in parts[1:] if re.match(r"[123]\. ", p)]
    if len(keep) != 3:
        sys.exit(f"refused: the guide has {len(keep)} of its 3 label sections")
    out = []
    for sec in keep:
        head, _, body = sec.partition("\n")
        out.append(f"<h3>{inline(head)}</h3>")
        rows, items, para = [], [], []

        def flush():
            if rows:
                cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
                body_rows = [c for c in cells[1:] if not all(re.fullmatch(r":?-+:?", x) for x in c)]
                out.append("<table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells[0]) + "</tr>"
                           + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body_rows)
                           + "</table>")
                rows.clear()
            if items:
                out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
                items.clear()
            if para:
                out.append(f"<p>{inline(' '.join(para))}</p>")
                para.clear()

        for line in body.splitlines():
            if line.startswith("|"):
                if items or para:
                    flush()
                rows.append(line)
            elif line.startswith("- "):
                if rows or para:
                    flush()
                items.append(line[2:].strip())
            elif line.startswith("  ") and items:
                items[-1] += " " + line.strip()
            elif not line.strip():
                flush()
            else:
                if rows or items:
                    flush()
                para.append(line.strip())
        flush()
    return "\n".join(out)

PROJECT = [("product", "Product: software for users or customers: an app, a service, a startup's or company's product; also apps people install (a browser extension, a desktop or mobile app) and a CLI whose main use is a company's own service"),
           ("internal system", "Internal system: code an organisation runs for itself, including a club, lab, team or community"),
           ("library or tool", "Library or tool: a reusable package, framework, command-line tool or plugin"),
           ("agent tooling", "Agent tooling: skills, prompts, configurations or servers for AI agents"),
           ("research code", "Research code: experiments, models or analyses in a research project"),
           ("writing", "Writing: mainly prose (a paper, thesis, book, blog, docs, notes)"),
           ("coursework or learning", "Coursework or learning: assignments, tutorials, practice projects"),
           ("personal", "Personal: a personal site, dotfiles, a hobby project or personal automation"),
           ("can't tell", "Can't tell from what is shown (for a session: code alone, with no word of its purpose)")]
DOMAIN = [(d, d[0].upper() + d[1:]) for d in ("web", "mobile", "ML or AI", "data", "infrastructure", "systems",
                                              "games or graphics", "science", "developer tools", "writing", "other",
                                              "can't tell")]
TASK = [("new feature", "New feature: add behaviour, a page, an endpoint, an option"),
        ("bug fix", "Bug fix: something is broken or wrong (an error, a failing test, wrong output)"),
        ("refactor", "Refactor: change the structure without changing behaviour"),
        ("tests", "Tests: write, run or fix tests"),
        ("setup or deploy", "Setup or deploy: environment, dependencies, build, CI, configuration, deployment"),
        ("writing", "Writing: docs, README, a paper, comments, notes"),
        ("experiments or analysis", "Experiments or analysis: run experiments, train or evaluate models, analyse data"),
        ("understanding", "Understanding: explain, answer a question, explore or plan without changing code"),
        ("review", "Review: review code, a diff or a pull request"),
        ("routine", "Routine: commit, push, merge, rename files, run a script"),
        ("can't tell", "Can't tell from what is shown")]

WHERE = ("the full guide is just below (open <b>The full guide</b>)" if GUIDE_MD else
         "<code>errata-data/docs/work-labels-guide.md</code>")
FULL = (f'<details class="guide"><summary>The full guide: every label, what it means, made-up examples and the rules'
        f'</summary>{guide_html(GUIDE_MD)}</details>' if GUIDE_MD else "")
SEND = ("<p>When every card is done, press <b>Export marks</b>: your browser saves one small file. Send that file back to "
        "the person who gave you this page. Work alone, and please do not look labels up anywhere else.</p>"
        if RATER else "")
GUIDE = f"""
<p>The labels say what the work is, never who the person is. Each card shows exactly what the model read. Pick the one
label that fits best; you will not see what the model decided. Full definitions and examples: {WHERE}.</p>
<p>Domain is the area of what the software does for its users: <i>developer tools</i> only when its users are developers
building software; a tool for anything else takes that thing's domain.</p>
<p>{len(cards)} cards, about an hour. A repository card asks two things (kind of project, domain), a session card one
(kind of task). Marks are saved in this browser as you go; press <b>Export marks</b> at the end.</p>
{SEND}
{FULL}
"""

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Work Labels Check</title>
<style>
:root {{ --bg:#fafaf8; --fg:#1d1d1b; --muted:#6b6b66; --card:#ffffff; --line:#e3e2dc; --accent:#2f5d9f; --done:#2e7d4f; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#171716; --fg:#ecebe6; --muted:#a3a29b;
  --card:#21211f; --line:#3a3936; --accent:#7ea6e0; --done:#6cc08f; }} }}
:root[data-theme="dark"] {{ --bg:#171716; --fg:#ecebe6; --muted:#a3a29b; --card:#21211f; --line:#3a3936; --accent:#7ea6e0;
  --done:#6cc08f; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0; padding:16px; }}
main {{ max-width:860px; margin:0 auto; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin:14px 0; }}
.meta, .q {{ color:var(--muted); font-size:13px; margin:8px 0 4px; }}
pre {{ white-space:pre-wrap; word-break:break-word; border:1px solid var(--line); border-radius:6px; padding:10px;
  max-height:340px; overflow:auto; font:13px/1.45 ui-monospace, monospace; background:var(--bg); }}
label {{ display:block; margin:2px 0; cursor:pointer; }}
.bar {{ position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); z-index:2;
  display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
button {{ background:var(--accent); color:#fff; border:0; border-radius:6px; padding:7px 12px; font-size:14px; cursor:pointer; }}
.marked {{ border-color:var(--done); }}
textarea {{ width:100%; box-sizing:border-box; margin-top:6px; background:var(--bg); color:var(--fg); border:1px solid var(--line);
  border-radius:6px; font:13px system-ui; }}
.guide {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:8px 14px; margin:10px 0; }}
.guide summary {{ cursor:pointer; font-weight:600; }}
.guide table {{ border-collapse:collapse; font-size:13px; margin:8px 0; display:block; overflow-x:auto; }}
.guide th, .guide td {{ border:1px solid var(--line); padding:4px 6px; text-align:left; vertical-align:top; }}
</style></head><body><main>
<h1>What is the work? A check of {len(cards)} labels</h1>
{GUIDE}
<div class="bar"><span id="progress"></span><button id="export">Export marks</button></div>
<div id="cards"></div>
</main>
<script>
const CARDS = {json.dumps(cards)};
const SAMPLE = {json.dumps(SAMPLE)};
const RATER = {json.dumps(RATER)};
const KEY = "e1-check4-" + (RATER ? RATER + "-" : "") + "marks-" + SAMPLE;
const Q = {{repo: [["project", "Kind of project", {json.dumps(PROJECT)}], ["domain", "Domain", {json.dumps(DOMAIN)}]],
           session: [["task", "Kind of task", {json.dumps(TASK)}]]}};
let marks = {{}};
try {{ marks = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (e) {{ marks = {{}}; }}
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(marks)); }} catch (e) {{}} progress(); }}
function complete(c) {{ return Q[c.kind].every(([f]) => marks[c.id] && marks[c.id][f]); }}
function progress() {{
  document.getElementById("progress").textContent = CARDS.filter(complete).length + " of " + CARDS.length + " done";
}}
function esc(s) {{ const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }}
const box = document.getElementById("cards");
CARDS.forEach((c, i) => {{
  const el = document.createElement("div");
  el.className = "card" + (complete(c) ? " marked" : "");
  const qs = Q[c.kind].map(([f, title, opts]) => `<div class="q">${{title}}</div>` + opts.map(([v, t]) =>
    `<label><input type="radio" name="c${{c.id}}-${{f}}" data-f="${{f}}" value="${{esc(v)}}" ${{marks[c.id] && marks[c.id][f] === v ? "checked" : ""}}> ${{esc(t)}}</label>`).join("")).join("");
  el.innerHTML = `<div class="meta">Card ${{i + 1}} of ${{CARDS.length}} · ${{c.kind === "repo" ? "a repository" : "a session"}}</div>
    <pre>${{esc(c.text)}}</pre>${{qs}}
    <textarea rows="1" placeholder="Note (optional)">${{esc((marks[c.id] && marks[c.id].note) || "")}}</textarea>`;
  el.querySelectorAll("input").forEach(r => r.addEventListener("change", () => {{
    marks[c.id] = Object.assign(marks[c.id] || {{}}, {{[r.dataset.f]: r.value}});
    el.classList.toggle("marked", complete(c)); save(); }}));
  el.querySelector("textarea").addEventListener("input", e => {{
    marks[c.id] = Object.assign(marks[c.id] || {{}}, {{note: e.target.value}}); save(); }});
  box.appendChild(el);
}});
document.getElementById("export").addEventListener("click", () => {{
  const blob = new Blob([JSON.stringify(RATER ? {{sample: SAMPLE, rater: RATER, marks}} : {{sample: SAMPLE, marks}}, null, 1)], {{type: "application/json"}});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = KEY + ".json"; a.click();
}});
progress();
</script></body></html>
"""
open(OUT, "w").write(page)
print("wrote", OUT, len(cards), "cards")

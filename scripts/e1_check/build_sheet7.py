"""The name check's page (check 7, 11-06): build_sheet5.py's page with this check's guide and choices.

python build_sheet7.py cards7.json out.html [SAMPLE] [--three]
--three (2026-11-08): the owner's three choices, a real person's name / not / can't tell, sample e1-check7b.
cards7.json: [{"id", "part", "who", "source", "agent", "before", "string", "after"}...] in the order to show. Marks
are exported from the page as JSON.
"""
import json
import sys

THREE = "--three" in sys.argv   # 2026-11-08, the owner's call: a person's name or not, three choices (sample e1-check7b)
if THREE:
    sys.argv.remove("--three")
cards = json.load(open(sys.argv[1]))
OUT = sys.argv[2]
SAMPLE = sys.argv[3] if len(sys.argv) > 3 else ("e1-check7b" if THREE else "e1-check7")

GUIDE = """
<p>Each card shows one name-like phrase that our released full text still holds, <mark>marked like this</mark>,
with up to 300 characters on each side. The masking removes names it finds in what developers type; a name only in an
agent's reply, a tool's output or other program text is left. Decide what the marked phrase is:</p>
<ul>
<li><b>A. A private person's name.</b> A real person who is not a public figure: a developer, a colleague, a client, a
family member, a commit's author. Count it even if the name also appears in a public repository's history.</li>
<li><b>B. A public figure's name.</b> A person known for public work: an author of a cited paper or book, a well-known
maintainer or speaker, a politician, a business leader, a celebrity, a historical figure.</li>
<li><b>C. Not a real person's name.</b> A product, a project, a company, a place, a phrase in any language, code, a
fictional character, or a placeholder name (Jane Doe, Alice Example).</li>
<li><b>D. Can't tell.</b></li>
</ul>
<p>{n} phrases, drawn from 18,000 released messages (3,000 for each kind of writer). About 30 to 40 minutes. Your marks
are saved in this browser as you go; press <b>Export marks</b> at the end and send me the file.</p>
"""
GUIDE3 = """
<p>Each card shows one name-like phrase that our released full text still holds, <mark>marked like this</mark>,
with up to 300 characters on each side. The masking removes names it finds in what developers type; a name only in an
agent's reply, a tool's output or other program text is left. Decide what the marked phrase is:</p>
<ul>
<li><b>A. A real person's name.</b> Any real person, known or not: a developer, a colleague, a client, a commit's
author, an author of a paper or book, a well-known maintainer, a public figure.</li>
<li><b>B. Not a real person's name.</b> A product, a project, a company, a place, a phrase in any language, code, a
fictional character, or a placeholder name (Jane Doe, Alice Example).</li>
<li><b>C. Can't tell.</b></li>
</ul>
<p>Please mark the cards yourself, from the text shown, without asking an AI model: the paper reports this as a
person's reading. {n} phrases, drawn from 18,000 released messages (3,000 for each kind of writer). About 20 to 30
minutes. Your marks are saved in this browser as you go; press <b>Export marks</b> at the end and send me the file.</p>
"""
GUIDE = (GUIDE3 if THREE else GUIDE).replace("{n}", str(len(cards)))
OPTS = ([["A", "A real person's name"], ["B", "Not a real person's name"], ["C", "Can't tell"]] if THREE else
        [["A", "A private person's name"], ["B", "A public figure's name"], ["C", "Not a real person's name"],
         ["D", "Can't tell"]])
page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Names Left: Check</title>
<style>
:root {{ --bg:#fafaf8; --fg:#1d1d1b; --muted:#6b6b66; --card:#ffffff; --line:#e3e2dc; --accent:#2f5d9f; --done:#2e7d4f;
  --mark:#ffe58a; --markfg:#1d1d1b; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#171716; --fg:#ecebe6; --muted:#a3a29b;
  --card:#21211f; --line:#3a3936; --accent:#7ea6e0; --done:#6cc08f; --mark:#8a6d00; --markfg:#fff; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0; padding:16px; }}
main {{ max-width:860px; margin:0 auto; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin:14px 0; }}
.meta {{ color:var(--muted); font-size:13px; margin-bottom:6px; }}
pre {{ white-space:pre-wrap; word-break:break-word; background:var(--bg); border:1px solid var(--line); border-radius:6px;
  padding:10px; max-height:340px; overflow:auto; font:13px/1.45 ui-monospace, monospace; }}
mark {{ background:var(--mark); color:var(--markfg); padding:0 2px; border-radius:3px; font-weight:600; }}
label {{ display:inline-block; margin:4px 14px 0 0; cursor:pointer; }}
.bar {{ position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); z-index:2;
  display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
button {{ background:var(--accent); color:#fff; border:0; border-radius:6px; padding:7px 12px; font-size:14px; cursor:pointer; }}
.marked {{ border-color:var(--done); }}
textarea {{ width:100%; box-sizing:border-box; margin-top:6px; background:var(--bg); color:var(--fg); border:1px solid var(--line);
  border-radius:6px; font:13px system-ui; }}
</style></head><body><main>
<h1>Names left: a check of {len(cards)} phrases</h1>
{GUIDE}
<div class="bar"><span id="progress"></span><button id="export">Export marks</button></div>
<div id="cards"></div>
</main>
<script>
const CARDS = {json.dumps(cards)};
const OPTS = {json.dumps(OPTS)};
const SAMPLE = {json.dumps(SAMPLE)};
const KEY = "e1-check-marks-" + SAMPLE;
let marks = {{}};
try {{ marks = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (e) {{ marks = {{}}; }}
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(marks)); }} catch (e) {{}} progress(); }}
function progress() {{
  const n = CARDS.filter(c => marks[c.id] && marks[c.id].answer).length;
  document.getElementById("progress").textContent = n + " of " + CARDS.length + " marked";
}}
function esc(s) {{ const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }}
const box = document.getElementById("cards");
CARDS.forEach((c, i) => {{
  const el = document.createElement("div");
  el.className = "card" + (marks[c.id] && marks[c.id].answer ? " marked" : "");
  const opts = OPTS.map(([v, t]) => `<label><input type="radio" name="c${{c.id}}" value="${{v}}" ${{marks[c.id] && marks[c.id].answer === v ? "checked" : ""}}> ${{v}}. ${{t}}</label>`).join("");
  el.innerHTML = `<div class="meta">Card ${{i + 1}} of ${{CARDS.length}} · written by: ${{esc(c.who)}} · source: ${{esc(c.source)}}${{c.agent ? " · agent: " + esc(c.agent) : ""}}</div>
    <pre>${{c.before ? "… " : ""}}${{esc(c.before)}}<mark>${{esc(c.string)}}</mark>${{esc(c.after)}}${{c.after ? " …" : ""}}</pre>${{opts}}
    <textarea rows="1" placeholder="Note (optional)">${{esc((marks[c.id] && marks[c.id].note) || "")}}</textarea>`;
  el.querySelectorAll("input").forEach(r => r.addEventListener("change", () => {{
    marks[c.id] = Object.assign(marks[c.id] || {{}}, {{answer: r.value}}); el.classList.add("marked"); save(); }}));
  el.querySelector("textarea").addEventListener("input", e => {{
    marks[c.id] = Object.assign(marks[c.id] || {{}}, {{note: e.target.value}}); save(); }});
  box.appendChild(el);
}});
document.getElementById("export").addEventListener("click", () => {{
  const blob = new Blob([JSON.stringify({{sample: SAMPLE, marks}}, null, 1)], {{type: "application/json"}});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = KEY + ".json"; a.click();
}});
progress();
</script></body></html>
"""
open(OUT, "w").write(page)
print("wrote", OUT, len(cards), "cards")

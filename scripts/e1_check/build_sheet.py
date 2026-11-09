"""Build the dataset paper's human-check sheet: one local HTML page, no pipeline answers in it.

python build_sheet.py cards.json out.html
cards.json: [{"id", "part", "agent", "source", "text", "len"}...] in the order to show. The key (pipeline answers)
stays in a separate file the page never sees. Marks are exported from the page as JSON.
"""
import html
import json
import sys

cards = json.load(open(sys.argv[1]))
OUT = sys.argv[2]
SAMPLE = sys.argv[3] if len(sys.argv) > 3 else "e1-check"

GUIDE = """
<p>Each card shows one message that an agent's log files as coming from the <b>user</b>. Decide who actually wrote the
text. Judge only from the text; you will not see what the pipeline decided.</p>
<ul>
<li><b>A. A person typed it.</b> Words a developer wrote to the agent, short or long, in any language. Text a person
pasted on purpose (an error log, a file path, a spec) counts as typed. A command the person typed (<code>/review</code>,
<code>/create_plan @file</code>) counts as typed.</li>
<li><b>B. A person's action, not typed text.</b> The person approved a plan the agent wrote ("Implement the following
plan: ..."), stopped the agent (an interrupt marker), or attached an image or a file.</li>
<li><b>C. A program or another agent wrote it.</b> Tool calls or tool output, environment details, instruction files
(AGENTS.md, CLAUDE.md, skills), the text a command or skill expands into, a hook's or scheduler's message, a report or
message from another agent, a transcript handed to a reviewer agent.</li>
<li><b>D. Can't tell.</b> Use it when the text alone does not say.</li>
</ul>
<p>Part 1 (cards 1-100) comes from SWE-chat v2's prompts; Part 2 from our own dataset. About 3 hours in all. Your marks
are saved in this browser as you go; press <b>Export marks</b> at the end and send me the file.</p>
"""
if SAMPLE.startswith("e1-check1-person"):   # a second person's page (2026-11-09): same cards and guide, their own marks
    GUIDE += """<p><b>Please mark the cards yourself</b>, from the text alone, without asking an AI model and without
seeing anyone else's marks.</p>
"""

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Who Typed It: Check</title>
<style>
:root {{ --bg:#fafaf8; --fg:#1d1d1b; --muted:#6b6b66; --card:#ffffff; --line:#e3e2dc; --accent:#2f5d9f; --done:#2e7d4f; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#171716; --fg:#ecebe6; --muted:#a3a29b;
  --card:#21211f; --line:#3a3936; --accent:#7ea6e0; --done:#6cc08f; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0; padding:16px; }}
main {{ max-width:860px; margin:0 auto; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin:14px 0; }}
.meta {{ color:var(--muted); font-size:13px; margin-bottom:6px; }}
pre {{ white-space:pre-wrap; word-break:break-word; background:var(--bg); border:1px solid var(--line); border-radius:6px;
  padding:10px; max-height:340px; overflow:auto; font:13px/1.45 ui-monospace, monospace; }}
label {{ display:inline-block; margin:4px 14px 0 0; cursor:pointer; }}
.bar {{ position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); z-index:2;
  display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
button {{ background:var(--accent); color:#fff; border:0; border-radius:6px; padding:7px 12px; font-size:14px; cursor:pointer; }}
.marked {{ border-color:var(--done); }}
textarea {{ width:100%; box-sizing:border-box; margin-top:6px; background:var(--bg); color:var(--fg); border:1px solid var(--line);
  border-radius:6px; font:13px system-ui; }}
</style></head><body><main>
<h1>Who typed it: a check of {len(cards)} messages</h1>
{GUIDE}
<div class="bar"><span id="progress"></span><button id="export">Export marks</button></div>
<div id="cards"></div>
</main>
<script>
const CARDS = {json.dumps(cards)};
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
  const more = c.len > c.text.length ? `\\n[... ${{c.len - c.text.length}} more characters not shown]` : "";
  const opts = [["A", "A person typed it"], ["B", "A person's action"], ["C", "A program or another agent"], ["D", "Can't tell"]]
    .map(([v, t]) => `<label><input type="radio" name="c${{c.id}}" value="${{v}}" ${{marks[c.id] && marks[c.id].answer === v ? "checked" : ""}}> ${{v}}. ${{t}}</label>`).join("");
  el.innerHTML = `<div class="meta">Card ${{i + 1}} of ${{CARDS.length}} · Part ${{c.part}} · agent: ${{esc(c.agent || "not named")}}${{c.source ? " · source: " + esc(c.source) : ""}}</div>
    <pre>${{esc(c.text + more)}}</pre>${{opts}}
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

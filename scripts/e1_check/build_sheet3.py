"""Build Part 3 of the human check: is a typed message pushback? One local HTML page, no model's answer in it.

python build_sheet3.py cards.json out.html SAMPLE_ID
cards.json: [{"id", "agent", "prev", "text", "len"}...]; "prev" is the agent's last reply before the message.
"""
import json
import sys

cards = json.load(open(sys.argv[1]))
OUT, SAMPLE = sys.argv[2], sys.argv[3]

GUIDE = """
<p>Each card shows the end of the agent's last reply, then the developer's next message. Judge the developer's
message as a reaction to that reply. You will not see what either model decided.</p>
<ul>
<li><b>A. Pushback.</b> The developer corrects a mistake or a misunderstanding, rejects or undoes the agent's work,
reports that it does not work, or steers away from what the agent did or proposed. ("I said X, not Y", "that broke
the build", "no, keep the old one", "it still shows the error".)</li>
<li><b>B. New information or a change of plan, with no sign the agent did anything wrong.</b> The developer adds
requirements or context, widens or narrows the scope, or switches to something else because they changed their mind.
("also make it dark mode", "here are the docs", "actually let's do the API first".)</li>
<li><b>C. Neither.</b> A new task, the next step, a question, an answer to the agent's question, approval. ("yes",
"commit and push", "how does this work?", "now add tests".)</li>
<li><b>D. Can't tell.</b></li>
</ul>
<p>80 cards, about 1 to 1.5 hours. Marks are saved in this browser as you go; press <b>Export marks</b> at the end.</p>
"""
if SAMPLE.startswith("e1-check3-person"):   # a second person's page (2026-11-09): same cards and guide, their own marks
    GUIDE += """<p><b>Please mark the cards yourself</b>, from the text alone, without asking an AI model and without
seeing anyone else's marks.</p>
"""

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pushback Check</title>
<style>
:root {{ --bg:#fafaf8; --fg:#1d1d1b; --muted:#6b6b66; --card:#ffffff; --line:#e3e2dc; --accent:#2f5d9f; --done:#2e7d4f;
  --agent:#f3f1ea; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#171716; --fg:#ecebe6; --muted:#a3a29b;
  --card:#21211f; --line:#3a3936; --accent:#7ea6e0; --done:#6cc08f; --agent:#2a2926; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0; padding:16px; }}
main {{ max-width:860px; margin:0 auto; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin:14px 0; }}
.meta, .who {{ color:var(--muted); font-size:13px; margin:4px 0; }}
pre {{ white-space:pre-wrap; word-break:break-word; border:1px solid var(--line); border-radius:6px; padding:10px;
  max-height:300px; overflow:auto; font:13px/1.45 ui-monospace, monospace; background:var(--bg); }}
pre.agent {{ background:var(--agent); max-height:200px; }}
label {{ display:block; margin:3px 0; cursor:pointer; }}
.bar {{ position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); z-index:2;
  display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
button {{ background:var(--accent); color:#fff; border:0; border-radius:6px; padding:7px 12px; font-size:14px; cursor:pointer; }}
.marked {{ border-color:var(--done); }}
textarea {{ width:100%; box-sizing:border-box; margin-top:6px; background:var(--bg); color:var(--fg); border:1px solid var(--line);
  border-radius:6px; font:13px system-ui; }}
</style></head><body><main>
<h1>Is it pushback? A check of {len(cards)} messages</h1>
{GUIDE}
<div class="bar"><span id="progress"></span><button id="export">Export marks</button></div>
<div id="cards"></div>
</main>
<script>
const CARDS = {json.dumps(cards)};
const SAMPLE = {json.dumps(SAMPLE)};
const KEY = "e1-check3-marks-" + SAMPLE;
let marks = {{}};
try {{ marks = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (e) {{ marks = {{}}; }}
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(marks)); }} catch (e) {{}} progress(); }}
function progress() {{
  const n = CARDS.filter(c => marks[c.id] && marks[c.id].answer).length;
  document.getElementById("progress").textContent = n + " of " + CARDS.length + " marked";
}}
function esc(s) {{ const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }}
const OPTS = [["A", "Pushback"], ["B", "New information or a change of plan, nothing wrong said"],
              ["C", "Neither"], ["D", "Can't tell"]];
const box = document.getElementById("cards");
CARDS.forEach((c, i) => {{
  const el = document.createElement("div");
  el.className = "card" + (marks[c.id] && marks[c.id].answer ? " marked" : "");
  const more = c.len > c.text.length ? `\\n[... ${{c.len - c.text.length}} more characters not shown]` : "";
  const opts = OPTS.map(([v, t]) => `<label><input type="radio" name="c${{c.id}}" value="${{v}}" ${{marks[c.id] && marks[c.id].answer === v ? "checked" : ""}}> ${{v}}. ${{t}}</label>`).join("");
  el.innerHTML = `<div class="meta">Card ${{i + 1}} of ${{CARDS.length}} · agent: ${{esc(c.agent || "not named")}}</div>
    <div class="who">The agent's last reply (its end):</div><pre class="agent">${{esc(c.prev ? "..." + c.prev : "(no reply before this message)")}}</pre>
    <div class="who">The developer's message:</div><pre>${{esc(c.text + more)}}</pre>${{opts}}
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

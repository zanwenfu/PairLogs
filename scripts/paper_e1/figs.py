"""The dataset paper's two figures, from measured JSON (m9b.json: our user-role entries; m5.json: SWE-chat's prompts).

python figs.py m9b.json m5.json outdir
"""
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

m9, m5, out = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({"font.size": 7.5, "font.family": "serif", "axes.spines.top": False, "axes.spines.right": False,
                     "pdf.fonttype": 42})
TYPED, ACTION, PROGRAM = "#2b6cb0", "#9ac2e6", "#d9822b"

# Figure 1: what agents' logs file in the user's role, by product (our six sources), share of entries
keep = ["Claude Code", "Codex", "Cursor", "GitHub Copilot", "OpenCode", "Pi", "Gemini CLI", "Aider", "(not named)"]
rows = {r["product"]: r for r in m9["entries_by_product"] if r["product"] in keep}
names = [p for p in keep if p in rows][::-1]
fig, ax = plt.subplots(figsize=(3.4, 2.1))
left = [0.0] * len(names)
for key, color, label in (("typed", TYPED, "typed by a person"), ("action", ACTION, "a person's action"),
                          ("program", PROGRAM, "a program")):
    vals = [rows[n][key] for n in names]
    ax.barh(names, vals, left=left, color=color, label=label, height=0.7)
    left = [a + b for a, b in zip(left, vals)]
for i, n in enumerate(names):
    ax.text(101, i, f"{rows[n]['n']:,}", va="center", fontsize=6, color="#555")
ax.set_xlim(0, 100)
ax.set_xlabel("% of entries the log files as the user's")
ax.legend(ncol=3, fontsize=6, frameon=False, loc="lower center", bbox_to_anchor=(0.42, 1.0), handlelength=1,
          columnspacing=1.0)
fig.tight_layout()
fig.savefig(out / "fig1_user_role.pdf")
fig.savefig(out / "fig1_user_role.png", dpi=200)

# Figure 2: SWE-chat's own pushback label, all labelled prompts vs only those a person typed, by agent
agents = ["Claude Code", "Codex", "Cursor", "OpenCode", "Copilot CLI", "Gemini CLI", "Pi"]
by = {r["agent"]: r for r in m5["by_agent"] if r["agent"] in agents}
agents = [a for a in agents if a in by]
fig, ax = plt.subplots(figsize=(3.4, 1.8))
x = range(len(agents))
w = 0.38
ax.bar([i - w / 2 for i in x], [by[a]["pb_all"] for a in agents], w, color=PROGRAM, label="every labelled prompt")
ax.bar([i + w / 2 for i in x], [by[a]["pb_typed"] for a in agents], w, color=TYPED, label="prompts a person typed")
ax.set_xticks(list(x), agents, rotation=20, ha="right")
ax.set_ylabel("% pushback")
ax.set_ylim(0, 75)
ax.legend(fontsize=6, frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2)
fig.tight_layout()
fig.savefig(out / "fig2_pushback.pdf")
fig.savefig(out / "fig2_pushback.png", dpi=200)
print("wrote", sorted(p.name for p in out.iterdir()))

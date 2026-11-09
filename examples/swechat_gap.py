"""Reproduce the paper's use case from released files only: SWE-chat v2's own pushback label, Claude Code against
Codex, on every labelled user prompt and on the prompts a person typed.

Inputs:
- SWE-chat v2's conversations table, from its own release on Hugging Face (SALT-NLP/SWE-chat, revision 202b071f,
  under its terms): conversations.parquet;
- errata-data's who-wrote-it table for those prompts, keyed by SWE-chat's turn_id (tier B,
  labels/swe_chat_prompts.parquet).

python examples/swechat_gap.py CONVERSATIONS.parquet SWE_CHAT_PROMPTS.parquet [--draws 2000] [--seed 0]

Prints the share of prompts SWE-chat labels as pushback (correction, rejection or failure report), by agent, on all
prompts and on typed ones, with 95% intervals from resampling repositories, and our own label on typed prompts.
Needs duckdb (pip install duckdb).
"""
import argparse
import random

import duckdb

PB = "('correction', 'rejection', 'failure_report')"
AGENTS = ("Claude Code", "Codex")

ap = argparse.ArgumentParser()
ap.add_argument("conversations")
ap.add_argument("prompts")
ap.add_argument("--draws", type=int, default=2000)
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()

con = duckdb.connect()
rows = con.execute(f"""
    select s.repo_id, s.agent, w.written_by,
           (s.prompt_pushback in {PB}) as sc_pb, w.pushback as our_pb
    from read_parquet(?) s join read_parquet(?) w using (turn_id)
    where s.turn_type = 'user_prompt' and s.prompt_pushback is not null and s.agent in {AGENTS}
""", [a.conversations, a.prompts]).fetchall()
print(f"{len(rows):,} labelled prompts of Claude Code and Codex matched to who wrote them")
print("who wrote them:", dict(con.execute("""select written_by, count(*) from read_parquet(?) group by 1
                                             order by 2 desc""", [a.prompts]).fetchall()))

# per repository and agent: counts for each measure, so a resample of repositories is a sum of their counts
MEASURES = {"every prompt, SWE-chat's label": lambda r: (True, r[3]),
            "typed only, SWE-chat's label": lambda r: (r[2] == "typed", r[3]),
            "typed only, our label": lambda r: (r[2] == "typed" and r[4] is not None, bool(r[4]))}
by_repo: dict = {}
for r in rows:
    for m, f in MEASURES.items():
        use, pb = f(r)
        if use:
            c = by_repo.setdefault(r[0], {}).setdefault((m, r[1]), [0, 0])
            c[0] += 1
            c[1] += pb
repos = sorted(by_repo)


def rate(sample, m, agent):
    n = sum(by_repo[x].get((m, agent), (0, 0))[0] for x in sample)
    k = sum(by_repo[x].get((m, agent), (0, 0))[1] for x in sample)
    return 100 * k / n if n else float("nan")


rng = random.Random(a.seed)
draws = [[rng.choice(repos) for _ in repos] for _ in range(a.draws)]
print(f"{len(repos)} repositories; 95% intervals from {a.draws:,} resamples of them\n")
for m in MEASURES:
    point = {ag: rate(repos, m, ag) for ag in AGENTS}
    gaps = sorted(rate(d, m, "Claude Code") - rate(d, m, "Codex") for d in draws)
    lo, hi = gaps[int(0.025 * len(gaps))], gaps[int(0.975 * len(gaps)) - 1]
    print(f"{m:32}  Claude Code {point['Claude Code']:5.1f}%  Codex {point['Codex']:5.1f}%  "
          f"gap {point['Claude Code'] - point['Codex']:+5.1f} [{lo:+.1f}, {hi:+.1f}]")

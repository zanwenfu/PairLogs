"""How SWE-Together's public tasks (Togetherbench/SWE-Together, fetched 2026-11-06 into swet_tasks.json: each task's
instruction.md, user_simulation_prompt.md and its `swechat` flag) open: the share whose first instruction, given to the
agent as the user's first message, is the text Claude Code sends when the developer approves a plan from plan mode
("Implement the following plan:"), among all tasks and among the tasks built from SWE-chat; and how many of those
tasks' simulator prompts mention plan mode.

python swet_check.py swet_tasks.json OUT.json
"""
import json
import sys

tasks = json.load(open(sys.argv[1]))
is_plan = lambda v: v["instruction.md"].lstrip().lower().startswith("implement the following plan")
from_swechat = lambda v: str(v["swechat"]).strip().lower() == "true"
assert all({"instruction.md", "user_simulation_prompt.md", "swechat"} <= set(v) for v in tasks.values())
out = {"tasks": len(tasks), "plan_opening": sum(map(is_plan, tasks.values())),
       "from_swechat": sum(map(from_swechat, tasks.values())),
       "plan_opening_from_swechat": sum(1 for v in tasks.values() if is_plan(v) and from_swechat(v)),
       "plan_tasks_whose_simulator_mentions_plan_mode": sum(1 for v in tasks.values() if is_plan(v)
                                                             and "plan mode" in v["user_simulation_prompt.md"].lower()),
       "first_message_said_verbatim_in_simulator": sum(1 for v in tasks.values() if is_plan(v) and
                                                       "implement the following plan" in v["user_simulation_prompt.md"].lower())}
json.dump(out, open(sys.argv[2], "w"), indent=1)
print(out)

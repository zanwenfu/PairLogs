# The dataset paper's measurements

Read-only scripts run at Duke on version 2026-10-11 (and its tiers), one Slurm job each; their JSON results and
the figures are kept with the paper draft. m1-m5: who wrote SWE-chat v2's labelled prompts (m5 is the final method);
m4: honest counts; m7: the human check's sample of our rows; m9: what logs file in the user's role (Figure 1);
p1, p2: the names stage's calibration; figs.py draws Figures 1 and 2.

De-duplication checks (version 2026-11-02, 11-03): dedup_spot.py reads 150 copies and their kept sessions again from
their own files and compares them row by row; dedup_contain.py measures how much of each copy's text its kept session
holds (every near-copy, every exact copy of the small strata, 300 of each large one); dedup_fuzzy.py re-reads those
under 90% allowing an export's rewrites; dedup_contain_shape.py and dedup_near_chain.py describe the ones not held;
same_conv.py counts kept sessions whose whole conversation another kept session repeats.

For the release (11-03): swe_prompts_who_typed.py is m16's matching kept per prompt by SWE-chat's own `turn_id`, with
no text; collect.tier_labels writes it into tier B as labels/swe_chat_prompts.parquet (docs/columns/labels.md).

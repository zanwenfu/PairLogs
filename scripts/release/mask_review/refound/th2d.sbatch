#!/bin/bash
#SBATCH -p compsci
#SBATCH -c 16
#SBATCH --mem=180G
#SBATCH -t 02:00:00
#SBATCH -J e1-th2d
#SBATCH -o /path/to/pairlogs-work/th2d.log
# Count only (nothing written): 325 values: the 319 plus 6 new written forms (8 found, 2 already listed; merged in Python: sort -u under the locale had dropped 8 of 327 lines) the first run missed (11-05 evening: two JDBC
# strings over a line break, a URI with its password unquoted, three base64 runs holding two Gemini keys), with
# code-th6 (f48e3f2: masking reads strings nested in a copied table). Expected: almost all of it in OpenHands feedback.
set -u
set -o pipefail
W=/path/to/pairlogs-work; C=$W/code-th6; P=$W/venv/bin/python; R=$W/release-fix2/2026-11-02
TS() { TZ=America/New_York date "+%-I:%M %p ET"; }
test "$(cat $C/COMMIT)" = "f48e3f2160f4dfe9484b52b49646d2214a405985" || { echo "STOP: code-th6 is not the commit"; exit 2; }
unset GITHUB_TOKEN GH_TOKEN HF_TOKEN
umask 077
export PYTHONPATH=$C PYTHONDONTWRITEBYTECODE=1
echo "$(TS) start"
cd /tmp
$P $C/scripts/release/mask_finding.py $R/a-names --values $W/private/th_values_final2.jsonl --label "trufflehog 3.99.8" --procs 12 --per-value $W/private/th_per_value_final2.json 2>&1 | grep -v RuntimeWarning
echo "exit $?"
echo "$(TS) end"

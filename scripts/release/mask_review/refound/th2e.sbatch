#!/bin/bash
#SBATCH -p compsci
#SBATCH -c 16
#SBATCH --mem=180G
#SBATCH -t 06:00:00
#SBATCH -J e1-th2e
#SBATCH -o /path/to/pairlogs-work/th2e.log
# The second masking pass on the masked release (11-05 evening), within the rule the user approved at about 5:40 pm ET
# (mask the 319 values and the forms the text holds): code-th6 (f48e3f2) masks the strings nested in a copied table
# (OpenHands feedback's trajectory, where the first pass left 31 of the 319 whole), and 6 written forms the first pass
# missed are added (two JDBC strings over a line break, a URI with its password unquoted, three base64 runs holding two
# Gemini keys): 325 values. Then Croissant and the tables again, the release check over both tiers (the not-secrets
# list of 11-05: + the path piece gitleaks cut), TruffleHog once more, and the originals. No network.
set -u
set -o pipefail
W=/path/to/pairlogs-work; C=$W/code-th6; P=$W/venv/bin/python; R=$W/release-fix2/2026-11-02
O=$W/fix11; S=$W/tmp-th2e
TS() { TZ=America/New_York date '+%-I:%M %p ET'; }
die() { echo "$(TS) STOP: $*"; exit 2; }
test "$(cat $C/COMMIT)" = "f48e3f2160f4dfe9484b52b49646d2214a405985" || die "code-th6 is not the commit"
grep -q "^exit 0" $W/th2d.log && grep -q '"values": 325' $W/th2d.log || die "the count pass did not pass"
test "$(wc -l < $W/private/th_values_final2.jsonl)" = 325 || die "the value list is not 325 lines"
test "$(wc -l < $W/private/not_secrets_2026-11-05.sha256)" = 326 || die "the not-secrets list is not 326 lines"
squeue -u "$USER" -h -o "%j" | grep -q "^e1-th2c$" && die "the release check of the earlier state is still running"
test -e $O && die "$O exists"
unset GITHUB_TOKEN GH_TOKEN HF_TOKEN
umask 077
ulimit -n 65536
export PYTHONPATH=$C PYTHONDONTWRITEBYTECODE=1
export ERRATA_GITLEAKS=$W/tools/gitleaks
export ERRATA_NOT_SECRETS=$W/private/not_secrets_2026-11-05.sha256 ERRATA_KNOWN_SECRETS=$W/private/known_secrets.txt
mkdir -p $O
echo "$(TS) start"
cd /tmp
echo "== 1. the mask"
$P $C/scripts/release/mask_finding.py $R/a-names --values $W/private/th_values_final2.jsonl --label "trufflehog 3.99.8 (second pass: nested text, written forms)" --procs 12 --write > $O/mask.txt 2> $O/mask.err; rc=$?
cat $O/mask.txt; test $rc = 0 || die "the mask (exit $rc)"
echo "$(TS) masked"
echo "== 2. Croissant again; the tables counted"
$P $C/scripts/croissant.py $R/a-names $R/a-names/croissant.json.th2e-tmp --version 2026-11-02 && mv $R/a-names/croissant.json.th2e-tmp $R/a-names/croissant.json || die "croissant"
$P $C/scripts/paper_e1/release_tables.py $R $O/release_tables.json > /dev/null || die "release tables"
cmp -s <($P -c "import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d, sort_keys=True))" $W/fix9/release_tables.json) <($P -c "import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d, sort_keys=True))" $O/release_tables.json) && echo "release tables: equal to the first pass's" || echo "release tables: DIFFER from the first pass's (read them)"
echo "== 3a. the release check over both tiers"
rm -rf $S; mkdir -p $S/data $S/state
(export ERRATA_DATA_ROOT=$S/data ERRATA_STATE_ROOT=$S/state; cd $C && $P -u $C/scripts/release/tier_check.py $R/a-names $R/b $S/work --procs 16) > $O/tier_check.log 2>&1; code=$?
grep -v RuntimeWarning $O/tier_check.log | tail -25
echo "$(TS) tier_check exit $code"
rm -rf $S
echo "== 3b. TruffleHog once more over tier A"
mkdir -p $S/data $S/state
(export ERRATA_DATA_ROOT=$S/data ERRATA_STATE_ROOT=$S/state; $P -u $C/scripts/release/second_scan.py $R/a-names $O/second_final.json $W/private/second_final2_hits.jsonl --trufflehog $W/tools/trufflehog-3.99.8/trufflehog --procs 16 --control) 2>&1 | grep -v RuntimeWarning | grep -v "units,.*GB" | head -25
rm -rf $S
$P $C/scripts/release/sort_hits.py $W/private/second_final2_hits.jsonl $O/second_final_sorted.json | head -2
$P - $W/private/th_values_final2.jsonl $W/private/second_final2_hits.jsonl <<'PY'
import json, sys, hashlib
from collections import Counter
masked = {hashlib.sha256(json.loads(l).encode("utf-8", "surrogatepass")).hexdigest() for l in open(sys.argv[1])}
hits = [json.loads(l) for l in open(sys.argv[2])]
left = {h["sha256"] for h in hits}
again = {h["sha256"]: h for h in hits if h["sha256"] in masked}
print(f"values found again after masking: {len(left)}; of them among the values given to mask: {len(again)}")
print("  those by detector/decoder:", dict(Counter(h["detector"] + "/" + h["decoder"] for h in again.values())))
print("  those by file:", dict(Counter(h["published_file"].split("/")[0] + "/" + h["published_file"].split("/")[1] for h in again.values())))
PY
echo "== 4. the originals against their checksum lists"
$P $C/scripts/fix_briefs/check_originals.py $W/zenodo/2026-11-02/MEMBERS.sha256 $W/release/2026-11-02 $W/deliver/errata-data-2026-11-02.sha256 $W/deliver --procs 16 || die "an original changed"
echo "$(TS) end"

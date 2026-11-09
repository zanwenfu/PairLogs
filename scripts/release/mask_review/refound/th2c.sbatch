#!/bin/bash
#SBATCH -p compsci
#SBATCH -c 16
#SBATCH --mem=180G
#SBATCH -t 03:00:00
#SBATCH -J e1-th2c
#SBATCH -o /path/to/pairlogs-work/th2c.log
# The release check again over both tiers of the masked release (th2b2 left it failing on one gitleaks finding: a path
# piece cut where gitleaks splits a 19.7 MB line, read 11-05 8:20 pm ET; its digest added to a new not-secrets list,
# not_secrets_2026-11-05.sha256 = the 10-27 list + 1 line). Free, no network, nothing in the release changed.
set -u
set -o pipefail
W=/path/to/pairlogs-work; C=$W/code-th5; P=$W/venv/bin/python; R=$W/release-fix2/2026-11-02
O=$W/fix10; S=$W/tmp-th2c
TS() { TZ=America/New_York date "+%-I:%M %p ET"; }
die() { echo "$(TS) STOP: $*"; exit 2; }
test "$(cat $C/COMMIT)" = "ff48a61251a7d7c4be1c92b714414442f0aab1ee" || die "code-th5 is not the commit"
test -e $O && die "$O exists"
test "$(wc -l < $W/private/not_secrets_2026-11-05.sha256)" = 326 || die "the new list is not 326 lines"
unset GITHUB_TOKEN GH_TOKEN HF_TOKEN
umask 077
ulimit -n 65536
export PYTHONPATH=$C PYTHONDONTWRITEBYTECODE=1
export ERRATA_GITLEAKS=$W/tools/gitleaks
export ERRATA_NOT_SECRETS=$W/private/not_secrets_2026-11-05.sha256 ERRATA_KNOWN_SECRETS=$W/private/known_secrets.txt
mkdir -p $O
echo "$(TS) start"
cd /tmp
rm -rf $S; mkdir -p $S/data $S/state
(export ERRATA_DATA_ROOT=$S/data ERRATA_STATE_ROOT=$S/state; cd $C && $P -u $C/scripts/release/tier_check.py $R/a-names $R/b $S/work --procs 16) > $O/tier_check.log 2>&1; code=$?
grep -v RuntimeWarning $O/tier_check.log | tail -25
echo "$(TS) tier_check exit $code"
rm -rf $S
echo "exit $code"

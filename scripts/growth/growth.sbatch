#!/bin/bash
#SBATCH -p compsci
#SBATCH -c 8
#SBATCH --mem=48G
#SBATCH -t 0-12:00:00
#SBATCH -J e1-growth
#SBATCH -o /path/to/pairlogs-work/growth/growth-%j.log
# The growth test (2026-11-06): the README's update, read-only for
# the version, run on the version's own GitHub repositories with no token, fresh empty data/state/build folders, fetch
# only (--no-build), then compared with the version's sessions (compare_growth.py, counts only).
#   STAGE=list    the inputs (list_repos.py): repos_all/batch0/rest.txt and the version's session index
#   STAGE=batch0  the first small batch (50 repositories at random); STAGE=rest the others (one job: see below)
# The code is the snapshot the tier-B rebuild test proved on 11-04 (its fresh read matched the version's hashes).
set -u
W=/path/to/pairlogs-work
G=$W/growth
STAGE=${STAGE:?list, batch0 or rest}
echo "stage $STAGE; start $(TZ=America/New_York date)"
if [ "$STAGE" = list ]; then
  $W/venv/bin/python -I $G/list_repos.py $W/run2/build-fix2/2026-11-02 $G/inputs
  echo "EXIT=$?; end $(TZ=America/New_York date)"; exit 0
fi
unset GITHUB_TOKEN GH_TOKEN HF_TOKEN
test -z "$(env | grep -E '^(GITHUB_TOKEN|GH_TOKEN|HF_TOKEN)=')" || { echo "a token is set: refusing"; exit 2; }
test ! -s "$HOME/.config/errata-data/env" || { echo "an env file exists: refusing (it may hold a token)"; exit 2; }
ONLY=$G/inputs/repos_$STAGE.txt
test -s $ONLY || { echo "no $ONLY: refusing"; exit 2; }
R=$G/$STAGE
test -e $R && { echo "$R exists: refusing (fresh folders only)"; exit 2; }
mkdir -p $R/data $R/state $R/build
export ERRATA_DATA_ROOT=$R/data ERRATA_STATE_ROOT=$R/state ERRATA_BUILD_ROOT=$R/build
export ERRATA_GITLEAKS=$W/tools/gitleaks PYTHONPATH=$W/rebuild-test/code
export ERRATA_NOT_SECRETS=$W/private/not_secrets_2026-10-27.sha256 ERRATA_KNOWN_SECRETS=$W/private/known_secrets.txt
test "$(wc -l < $ERRATA_NOT_SECRETS)" -eq 325 -a "$(wc -l < $ERRATA_KNOWN_SECRETS)" -eq 160 || { echo "masking lists not the 10-27 ones: refusing"; exit 2; }
test "$($W/tools/gitleaks version)" = "8.30.1" || { echo "gitleaks 8.30.1 missing: refusing"; exit 2; }
cd $W/rebuild-test/code
echo "$(wc -l < $ONLY) repositories; code snapshot $(cat SNAPSHOT)"
T0=$(date +%s)
LOG=$G/growth-$SLURM_JOB_ID.log
# Gentle: 4 fetches in flight (six jobs of 8 at once, 11-06 2:52 am, made GitHub refuse anonymous git from Duke).
# A guard stops the run if GitHub starts refusing again: more than MAX403 refusals and it is killed, not left to
# count refused repositories as gone.
MAX403=${MAX403:-25}
$W/venv/bin/python -u -m collect.cli update --only-file $ONLY --source entire --source specstory --source aider \
    --cpu 8 --workers ${WORKERS:-4} --no-build &
PID=$!
while kill -0 $PID 2>/dev/null; do
  n=$(grep -c "returned error: 403" $LOG)
  u=$(grep -o -E "\(([0-9]+) repositories unreachable\)" $LOG | grep -o -E "[0-9]+" | head -1)
  if [ "$n" -gt "$MAX403" ] || [ "${u:-0}" -gt "${MAXUNREACH:-200}" ]; then
    echo "GUARD: $n refusals logged, ${u:-0} repositories unreachable at the probe: stopping the fetch"
    kill $PID; sleep 20; kill -9 $PID 2>/dev/null
  fi
  sleep 30
done
wait $PID; code=$?
echo "EXIT=$code; fetch seconds $(( $(date +%s) - T0 ))"
du -sh $R/data 2>/dev/null
grep -o -E "\] [^ :]+/[^ :]+: ERROR" $LOG | sed -E 's/^\] //; s/: ERROR$//' | sort -u > $R/fetch_errors.txt
echo "repositories with a fetch error: $(wc -l < $R/fetch_errors.txt); refusals (403) logged: $(grep -c 'returned error: 403' $LOG)"
$W/venv/bin/python -I $G/reprobe.py $G/inputs/version_index.parquet $ONLY $R/state/ledger.sqlite $R/reprobe_errors.txt $R/gone.txt
cat $R/fetch_errors.txt $R/reprobe_errors.txt | sort -u > $R/errors.txt
$W/venv/bin/python -I $G/compare_growth.py $G/inputs/version_index.parquet $ONLY $R/state/ledger.sqlite $R/compare.json $R/errors.txt
echo "compare EXIT=$?; end $(TZ=America/New_York date)"

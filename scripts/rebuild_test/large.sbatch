#!/bin/bash
#SBATCH -p compsci
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 0-04:00:00
#SBATCH -J e1-rblarge
#SBATCH -o /path/to/pairlogs-work/rebuild-test/rblarge-%j.log
# Tier B's rebuild test (2026-11-04): the released code's snapshot (code_snapshot.sh of errata-data HEAD), no GitHub
# token, fresh empty data/state/build folders, the README's command restricted to the GitHub sources:
#   collect update --only-file <repos> --source entire --source specstory --source aider
# The second test (larger repositories, sample_large.py): STAGE=large-trial (the one repository in TRIAL) or large. The masking lists are the build's (the
# private ones the released version was built with), so the rebuilt files can be compared by hash.
set -u
W=/path/to/pairlogs-work
T=$W/rebuild-test
STAGE=${STAGE:?large-trial or large}
ONLY=${ONLY:-$T/large-sample/repos.txt}
unset GITHUB_TOKEN GH_TOKEN HF_TOKEN
test -z "$(env | grep -E '^(GITHUB_TOKEN|GH_TOKEN|HF_TOKEN)=')" || { echo "a token is set: refusing"; exit 2; }
test ! -s "$HOME/.config/errata-data/env" || { echo "an env file exists: refusing (it may hold a token)"; exit 2; }
R=$T/$STAGE
test -e $R && { echo "$R exists: refusing (fresh folders only)"; exit 2; }
mkdir -p $R/data $R/state $R/build
export ERRATA_DATA_ROOT=$R/data ERRATA_STATE_ROOT=$R/state ERRATA_BUILD_ROOT=$R/build
export ERRATA_GITLEAKS=$W/tools/gitleaks PYTHONPATH=$T/code
export ERRATA_NOT_SECRETS=$W/private/not_secrets_2026-10-27.sha256 ERRATA_KNOWN_SECRETS=$W/private/known_secrets.txt
test "$(wc -l < $ERRATA_NOT_SECRETS)" -eq 325 -a "$(wc -l < $ERRATA_KNOWN_SECRETS)" -eq 160 || { echo "masking lists not the 10-27 ones: refusing"; exit 2; }
test "$($W/tools/gitleaks version)" = "8.30.1" || { echo "gitleaks 8.30.1 missing: refusing"; exit 2; }
if [ "$STAGE" = large-trial ]; then echo "$TRIAL" > $R/only.txt; else cp $ONLY $R/only.txt; fi
EXTRA=""; [ "$STAGE" = dry ] && EXTRA="--dry-run"
cd $T/code
echo "stage $STAGE; $(wc -l < $R/only.txt) repositories; code snapshot $(cat $T/code/SNAPSHOT 2>/dev/null); start $(TZ=America/New_York date)"
$W/venv/bin/python -u -m collect.cli update --only-file $R/only.txt --source entire --source specstory --source aider \
    --cpu 8 --workers 8 $EXTRA
echo "EXIT=$?"
du -sh $R/data $R/build 2>/dev/null
ls $R/build
echo "end $(TZ=America/New_York date)"

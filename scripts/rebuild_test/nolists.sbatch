#!/bin/bash
#SBATCH -p compsci
#SBATCH -c 4
#SBATCH --mem=32G
#SBATCH -t 0-02:00:00
#SBATCH -J e1-rbnolist
#SBATCH -o /path/to/pairlogs-work/rebuild-test/nolists-%j.log
# Tier B's rebuild as a public user runs it: the full stage's fetched data (copied, not fetched again) built WITHOUT
# the maintainer's private masking lists (not-secret digests, known secret texts), which are not released.
set -u
W=/path/to/pairlogs-work
T=$W/rebuild-test
R=$T/nolists
test -e $R && { echo "$R exists: refusing"; exit 2; }
unset GITHUB_TOKEN GH_TOKEN HF_TOKEN ERRATA_NOT_SECRETS ERRATA_KNOWN_SECRETS
mkdir -p $R/build && cp -a $T/full/data $R/data && cp -a $T/full/state $R/state
export ERRATA_DATA_ROOT=$R/data ERRATA_STATE_ROOT=$R/state ERRATA_BUILD_ROOT=$R/build
export ERRATA_GITLEAKS=$W/tools/gitleaks PYTHONPATH=$T/code
cd $T/code
echo "no lists; start $(TZ=America/New_York date)"
$W/venv/bin/python -u -m collect.cli update --no-fetch --full --cpu 4 --name 2026-11-04
echo "EXIT=$?"
echo "end $(TZ=America/New_York date)"

#!/bin/bash
#SBATCH -p compsci
#SBATCH -c 8
#SBATCH --mem=128G
#SBATCH -t 3:00:00
#SBATCH -J keys1102
#SBATCH -o /path/to/pairlogs-work/keys1102.log
set -u
W=/path/to/pairlogs-work
echo "start $(TZ=America/New_York date)"
ls -la $W/run2/build/2026-11-02/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet || exit 3
$W/venv/bin/python -u $W/scripts/keys_1102.py $W/keys1102
code=$?
echo "end $(TZ=America/New_York date) exit $code"

#!/bin/bash
#SBATCH -p compsci
#SBATCH -c 4
#SBATCH --mem=32G
#SBATCH -t 01:00:00
#SBATCH -J e1-rblcmp
#SBATCH -o /path/to/pairlogs-work/rebuild-test/rblarge-compare-%j.log
# The second rebuild test compared: compare.py (as the first test) and diag_changed.py (why files differ).
cd /path/to/pairlogs-work/rebuild-test
V=$(ls -d large/build/2026-* | tail -1)
echo "rebuilt version $V"
/path/to/pairlogs-work/venv/bin/python -I compare.py large-sample/sample.parquet $V /path/to/pairlogs-work/release/2026-11-02/b large/compare.json
/path/to/pairlogs-work/venv/bin/python -I diag_changed.py large-sample/sample.parquet $V large/data/raw /path/to/pairlogs-work/run2/data/raw large/diag_changed.json
echo "exit $?"

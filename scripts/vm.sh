#!/bin/bash
# Run the collector on the run machine from a laptop: sync the committed code, start the machine, run
# `collect update` there under a busy marker, and deallocate the machine when the run has ended and no other
# marker remains. The laptop only waits.
#
#   scripts/vm.sh run [collect update arguments ...]    e.g. scripts/vm.sh run --source entire --dry-run
#   scripts/vm.sh status                                 the machine's state, markers, and the last run's log
#   scripts/vm.sh stop                                   deallocate, unless another marker remains
#
# Which machine, from the environment (never committed): ERRATA_VM_HOST (user@address), ERRATA_VM_GROUP and
# ERRATA_VM_NAME (the cloud resource group and machine), ERRATA_VM_DATA (data root on the machine, e.g. the data
# disk). Credentials stay on the machine, in ~/.config/errata-data/env.
set -u
: "${ERRATA_VM_HOST:?set ERRATA_VM_HOST}" "${ERRATA_VM_GROUP:?set ERRATA_VM_GROUP}" "${ERRATA_VM_NAME:?set ERRATA_VM_NAME}"
DATA="${ERRATA_VM_DATA:-errata-data-run}"
# Absolute (a data disk) or under the machine's home: ROOT for commands run there, SCP_ROOT for copying from it.
case "$DATA" in /*) ROOT="$DATA"; SCP_ROOT="$DATA" ;; *) ROOT="\$HOME/$DATA"; SCP_ROOT="~/$DATA" ;; esac
MARK=errata-data
SSH=(ssh -o ConnectTimeout=15 -o BatchMode=yes "$ERRATA_VM_HOST")
cd "$(dirname "$0")/.." || exit 2

wait_ssh() {
    for _ in $(seq 1 60); do "${SSH[@]}" true < /dev/null 2>/dev/null && return 0; sleep 15; done
    echo "the machine did not answer ssh in 15 minutes"; return 1
}

stop_if_idle() {
    left=$("${SSH[@]}" 'ls ~/busy 2>/dev/null | wc -l' < /dev/null)
    if [ "${left:-1}" = 0 ]; then
        az vm deallocate -g "$ERRATA_VM_GROUP" -n "$ERRATA_VM_NAME" && echo "deallocated"
    else
        echo "not deallocated: $left busy marker(s) remain: $("${SSH[@]}" 'ls ~/busy' < /dev/null | tr '\n' ' ')"
    fi
}

case "${1:-}" in
run)
    shift
    for a in "$@"; do case "$a" in *[\'\"]*) echo "refused: quotes in an argument ($a)"; exit 2 ;; esac; done
    [ -z "$(git status --porcelain -- collect pyproject.toml)" ] || echo "note: uncommitted changes are not synced; the committed code runs"
    az vm start -g "$ERRATA_VM_GROUP" -n "$ERRATA_VM_NAME" --no-wait
    wait_ssh || exit 1
    # Before anything is synced: a sync replaces the code under a run that is going.
    if "${SSH[@]}" "test -e ~/busy/$MARK" < /dev/null; then
        echo "refused: a collect run is already going (~/busy/$MARK on the machine)"; exit 2
    fi
    git archive --format=tar HEAD | "${SSH[@]}" 'mkdir -p ~/errata-data && rm -rf ~/errata-data/collect ~/errata-data/scripts ~/errata-data/tests && tar -x -C ~/errata-data'
    "${SSH[@]}" 'cd ~/errata-data && { [ -d .venv ] || python3 -m venv .venv; } && .venv/bin/pip install -q -e . && .venv/bin/python tests/check_scanner.py > ~/errata-data-scanner-check.log 2>&1 && echo "scanner check passed" || { echo "scanner check FAILED: see ~/errata-data-scanner-check.log"; exit 1; }' < /dev/null || exit 1
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    args=$(printf ' %q' "$@")
    "${SSH[@]}" "mkdir -p ~/busy ~/errata-data-logs && touch ~/busy/$MARK && setsid -f bash -c 'cd ~/errata-data && ERRATA_DATA_ROOT=$ROOT/data ERRATA_STATE_ROOT=$ROOT/state .venv/bin/collect update$args > ~/errata-data-logs/$stamp.log 2>&1 & echo \$! > ~/errata-data-logs/$stamp.pid; wait \$!; echo \$? > ~/errata-data-logs/$stamp.exit; rm -f ~/busy/$MARK' < /dev/null > /dev/null 2>&1" < /dev/null
    echo "running; log: ~/errata-data-logs/$stamp.log on the machine"
    sleep 30  # the run writes its process id and log as it starts; checked before then, it would look gone
    # Until the run writes its exit status, or its process is gone without one (killed: the marker is then cleared).
    # The process id recorded is collect's own, not its wrapper's: a signal meant for collect, sent to the wrapper,
    # would end the wrapper while collect ran on, and this loop would take the run for ended.
    # A log silent for an hour is reported, not stopped: a long download or a rate-limit wait can be quiet.
    while ! "${SSH[@]}" "test -f ~/errata-data-logs/$stamp.exit" < /dev/null 2>/dev/null; do
        state=$("${SSH[@]}" "kill -0 \$(cat ~/errata-data-logs/$stamp.pid) 2>/dev/null && echo alive || echo gone; echo \$(( \$(date +%s) - \$(stat -c %Y ~/errata-data-logs/$stamp.log) ))" < /dev/null 2>/dev/null)
        case "$state" in
        gone*)
            sleep 20
            "${SSH[@]}" "test -f ~/errata-data-logs/$stamp.exit" < /dev/null 2>/dev/null && break
            echo "the run ended without an exit status (killed?); clearing its marker"
            "${SSH[@]}" "rm -f ~/busy/$MARK; echo killed > ~/errata-data-logs/$stamp.exit" < /dev/null
            break ;;
        alive*)
            quiet=${state##*$'\n'}
            if [ "${quiet:-0}" -gt 3600 ] 2>/dev/null; then
                echo "$(date -u +%H:%M) the log has been silent for $((quiet / 60)) minutes; its last lines:"
                "${SSH[@]}" "tail -3 ~/errata-data-logs/$stamp.log" < /dev/null
                echo "  (to see where it is: kill -USR1 \$(cat ~/errata-data-logs/$stamp.pid) on the machine writes every thread's stack to the log)"
            fi ;;
        *) echo "$(date -u +%H:%M) the machine did not answer; trying again" ;;
        esac
        sleep 300
    done
    # The ledger, copied off the machine after every run: a consistent copy made there first (it is in WAL mode,
    # so the main file alone can miss the last writes), the last five kept.
    mkdir -p "$HOME/.errata-data-backup"
    "${SSH[@]}" "cd ~/errata-data && .venv/bin/python -c 'import sqlite3, sys; s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2]); s.backup(d); d.close()' $ROOT/state/ledger.sqlite ~/errata-data-ledger-copy.sqlite" < /dev/null \
        && scp -q "$ERRATA_VM_HOST:errata-data-ledger-copy.sqlite" "$HOME/.errata-data-backup/ledger-$stamp.sqlite" \
        && echo "ledger copied to ~/.errata-data-backup/ledger-$stamp.sqlite" || echo "note: the ledger could not be copied off the machine"
    ls -t "$HOME"/.errata-data-backup/ledger-*.sqlite 2>/dev/null | tail -n +6 | while read -r f; do rm -f "$f"; done
    echo "run ended with exit $("${SSH[@]}" "cat ~/errata-data-logs/$stamp.exit" < /dev/null)"
    "${SSH[@]}" "tail -20 ~/errata-data-logs/$stamp.log" < /dev/null
    stop_if_idle
    ;;
status)
    az vm get-instance-view -g "$ERRATA_VM_GROUP" -n "$ERRATA_VM_NAME" --query "instanceView.statuses[1].displayStatus" -o tsv
    "${SSH[@]}" 'echo "markers: $(ls ~/busy 2>/dev/null | tr "\n" " ")"; f=$(ls -t ~/errata-data-logs/*.log 2>/dev/null | head -1); [ -n "$f" ] && tail -5 "$f"' < /dev/null 2>/dev/null || echo "(not reachable)"
    ;;
stop)
    stop_if_idle
    ;;
*)
    sed -n '2,13p' "$0"; exit 2
    ;;
esac

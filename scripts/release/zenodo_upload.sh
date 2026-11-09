#!/bin/sh
# Upload a release's Zenodo set into ONE NEW DRAFT record, each file streamed from Duke through this laptop (nothing is
# saved here), then compare every file's MD5 with the one Zenodo reports. The body goes with its length and without
# chunked encoding (curl chunks a piped upload unless told not to; the stand-in server caught it, 2026-11-04). It
# never publishes and sets no metadata: the owner fills the record in on zenodo.org (title, authors, licence, public
# or restricted) and presses publish.
#
#   export ZENODO_TOKEN=...      (zenodo.org > Applications > Personal access tokens; scopes deposit:write)
#   scripts/release/zenodo_upload.sh --dry-run                    # lists what it would send; no call to Zenodo
#   scripts/release/zenodo_upload.sh --only tierA-work_labels.zip # first, one small file into a new draft
#   scripts/release/zenodo_upload.sh --draft ID                   # then every file not yet in that draft
#
# Options: --sandbox (sandbox.zenodo.org, its own token), --draft ID (add to an existing draft), --only NAME.
# The token is sent in a header, never in a URL, and never printed or logged.
set -eu
DUKE="ssh -o BatchMode=yes USER@HOST"
Z=/path/to/pairlogs-work/zenodo/2026-11-02
API=https://zenodo.org/api
DRAFT= ONLY= DRY=
while [ $# -gt 0 ]; do
    case $1 in
        --sandbox) API=https://sandbox.zenodo.org/api ;;
        --api) API=$2; shift ;;   # a stand-in server, for testing this script only
        --draft) DRAFT=$2; shift ;;
        --only) ONLY=$2; shift ;;
        --dry-run) DRY=1 ;;
        *) echo "unknown option $1"; exit 2 ;;
    esac
    shift
done
LOG=$(mktemp -d)/zenodo-upload.log
say() { echo "$(TZ=America/New_York date '+%-I:%M:%S %p ET') $*" | tee -a "$LOG"; }

LIST=$($DUKE "cd $Z && cat MD5SUMS" </dev/null)
[ -n "$LIST" ] || { echo "no MD5SUMS at Duke: refusing"; exit 2; }
n=$(printf '%s\n' "$LIST" | wc -l | tr -d ' ')
[ "$n" -le 99 ] || { echo "$n files: more than one Zenodo record takes"; exit 2; }
say "the set at Duke: $n files"

if [ -n "$DRY" ]; then
    printf '%s\n' "$LIST" | while read -r md5 name; do
        size=$($DUKE "stat -c %s $Z/$name" </dev/null)
        [ -z "$ONLY" ] || [ "$name" = "$ONLY" ] && echo "would send $name ($size bytes, md5 $md5)"
    done
    exit 0
fi
[ -n "${ZENODO_TOKEN:-}" ] || { echo "set ZENODO_TOKEN first"; exit 2; }
AUTH="Authorization: Bearer $ZENODO_TOKEN"
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

if [ -z "$DRAFT" ]; then
    DRAFT=$(curl -sS --fail -X POST -H "$AUTH" -H "Content-Type: application/json" -d '{}' "$API/deposit/depositions" \
        | json 'd["id"]')
    say "new draft $DRAFT (not published)"
fi
DEP=$(curl -sS --fail -H "$AUTH" "$API/deposit/depositions/$DRAFT")
BUCKET=$(printf '%s' "$DEP" | json 'd["links"]["bucket"]')
HAVE=$(printf '%s' "$DEP" | json '"\n".join(f["filename"] for f in d.get("files", []))')

printf '%s\n' "$LIST" | while read -r md5 name; do
    [ -z "$ONLY" ] || [ "$name" = "$ONLY" ] || continue
    if printf '%s\n' "$HAVE" | grep -qx "$name"; then say "already in the draft: $name"; continue; fi
    size=$($DUKE "stat -c %s $Z/$name" </dev/null)
    say "sending $name ($size bytes)"
    got=$($DUKE "cat $Z/$name" </dev/null | curl -sS --fail -X PUT -H "$AUTH" -H "Content-Type: application/octet-stream" \
        -H "Content-Length: $size" -H "Transfer-Encoding:" --upload-file - "$BUCKET/$name" | json 'd["checksum"]')
    if [ "$got" = "md5:$md5" ]; then say "  ok, Zenodo's md5 matches"; else say "  MISMATCH: Zenodo says $got"; fi
done
bad=$(grep -c MISMATCH "$LOG" || true)
say "mismatches: $bad"
say "done. Draft: ${API%/api}/uploads/$DRAFT  (review it there; publishing is yours). Log: $LOG"

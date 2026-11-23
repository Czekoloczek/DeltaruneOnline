#!/bin/sh
# Lockstep check for drnet.dll without the game: runs native/build/test_drnet.exe as a host and its clients
# under Wine on this machine and checks that every side received exactly the same input (same running
# checksum, no desync). Used by .github/workflows/tests.yml; works the same on a Linux PC.
#   sh native/test_pair.sh [lag_ms] [frames] [players]      defaults: 0 450 2
# Needs native/build/drnet.dll and native/build/test_drnet.exe (sh native/build.sh) and Wine.
set -eu
lag=${1:-0}
frames=${2:-450}
players=${3:-2}
cd "$(dirname "$0")/build"
[ -f drnet.dll ] && [ -f test_drnet.exe ] || { echo "build first: sh native/build.sh"; exit 2; }
WINE=${WINE:-wine}
export WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="    # no Mono/Gecko install prompts
export WINEPREFIX="${WINEPREFIX:-${XDG_CACHE_HOME:-$HOME/.cache}/dronline-test/pair}"
mkdir -p "$(dirname "$WINEPREFIX")"                             # Wine creates the prefix, but not its parent
# Create the prefix before two copies race for it; a failure here shows up as "did not finish" below.
"$WINE" cmd /c exit >/dev/null 2>&1 || echo "warning: creating the Wine prefix $WINEPREFIX failed"
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

echo "== $players players, $frames frames, ${lag} ms test latency each way"
timeout 180 "$WINE" test_drnet.exe host "$players" 1 "$frames" "$lag" > "$out/1.txt" 2>&1 &
pids=$!
sleep 1
p=2
while [ "$p" -le "$players" ]; do
    timeout 180 "$WINE" test_drnet.exe client "$players" "$p" "$frames" "$lag" > "$out/$p.txt" 2>&1 &
    pids="$pids $!"
    p=$((p + 1))
done
for pid in $pids; do wait "$pid" || true; done

ok=1
first=""
p=1
while [ "$p" -le "$players" ]; do
    grep -E 'frame +[0-9]+ sum' "$out/$p.txt" | tail -1 | sed "s/^/  player $p: /" || true
    done_line=$(grep 'final sum' "$out/$p.txt" || true)
    if [ -z "$done_line" ]; then
        echo "  player $p did not finish:"; sed 's/^/    /' "$out/$p.txt" | tail -5
        ok=0
    else
        sum=$(echo "$done_line" | sed -E 's/.*final sum ([0-9a-f]+).*/\1/')
        desync=$(echo "$done_line" | sed -E 's/.*desync (-?[0-9]+).*/\1/')
        echo "  player $p: final sum $sum, desync $desync"
        [ "$desync" = "-1" ] || ok=0
        if [ -z "$first" ]; then first=$sum; elif [ "$sum" != "$first" ]; then ok=0; fi
    fi
    p=$((p + 1))
done
if [ "$ok" = 1 ]; then echo "OK: all $players sides got identical input"; else echo "FAIL: sides differ or did not finish"; exit 1; fi

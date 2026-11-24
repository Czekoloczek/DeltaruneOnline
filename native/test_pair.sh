#!/bin/sh
# Lockstep check for drnet.dll without the game: runs native/build/test_drnet.exe as a host and its clients
# on this machine and checks that every side received exactly the same input (same running checksum, no
# desync). On Linux it runs them under Wine (WINE=path picks another Wine, e.g. Proton's files/bin/wine);
# on Windows (Git Bash) it runs them natively. Used by .github/workflows/tests.yml.
#   sh native/test_pair.sh [lag_ms] [frames] [players] [late_s]      defaults: 0 450 2 0
# late_s: the last client starts that many seconds after the others, as when a friend joins late. The others must
# wait for it (test_drnet connects with a 30 s timeout) instead of giving up after the 10 s exchange timeout.
# Needs native/build/drnet.dll and native/build/test_drnet.exe (sh native/build.sh).
set -eu
lag=${1:-0}
frames=${2:-450}
players=${3:-2}
late=${4:-0}
cd "$(dirname "$0")/build"
[ -f drnet.dll ] && [ -f test_drnet.exe ] || { echo "build first: sh native/build.sh"; exit 2; }
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) native=1 ;;
    *) native=0 ;;
esac
if [ "$native" = 1 ]; then
    where="natively on Windows"
else
    WINE=${WINE:-wine}
    export WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="    # no Mono/Gecko install prompts
    export WINEPREFIX="${WINEPREFIX:-${XDG_CACHE_HOME:-$HOME/.cache}/dronline-test/pair}"
    unset TMPDIR                                                    # the Wine 9.0 of Ubuntu 24.04 aborts at start when it is set
    mkdir -p "$(dirname "$WINEPREFIX")"                             # Wine creates the prefix, but not its parent
    # Create the prefix before two copies race for it; a failure here shows up as "did not finish" below.
    "$WINE" cmd /c exit >/dev/null 2>&1 || echo "warning: creating the Wine prefix $WINEPREFIX failed"
    where="under $WINE"
fi
# coreutils timeout stops a side that hangs; Windows has its own unrelated timeout.exe, so check first
if timeout --version >/dev/null 2>&1; then to="timeout 180"; else to=""; fi
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
stage="$out/bin"
mkdir -p "$stage"
cp drnet.dll test_drnet.exe "$stage/"
printf 'DELTARUNE_PAIR_TEST_DATA\n' > "$stage/data.win"
cd "$stage"

echo "== $players players, $frames frames, ${lag} ms test latency each way$([ "$late" -gt 0 ] && echo ", the last one ${late} s late"), $where"
start_side() {   # role player outfile
    if [ "$native" = 1 ]; then
        $to ./test_drnet.exe "$1" "$players" "$2" "$frames" "$lag" > "$3" 2>&1 &
    else
        $to "$WINE" test_drnet.exe "$1" "$players" "$2" "$frames" "$lag" > "$3" 2>&1 &
    fi
}
start_side host 1 "$out/1.txt"
pids=$!
sleep 1
p=2
while [ "$p" -le "$players" ]; do
    [ "$p" -eq "$players" ] && [ "$late" -gt 0 ] && sleep "$late"
    start_side client "$p" "$out/$p.txt"
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

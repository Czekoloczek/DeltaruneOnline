#!/bin/sh
# Handshake checks for drnet.dll without the game: verifies that matching data.win files connect with
# identical checksums, mismatched data.win files are refused promptly with both SHA-256 prefixes (verified
# against Python's hashlib.sha256), missing data.win fails immediately on start even when both peers lack it,
# mismatched player counts are refused promptly, and with 3 players a client that leaves the lobby before
# everybody is there (with a goodbye, or killed without one) is no longer counted by the host (issue #58).
# Runs natively on Windows (Git Bash) or under Wine on Linux. Used by tools/verify.sh and CI.
set -eu
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$(sh "$REPO_ROOT/tools/find_python.sh")"
case "$PY" in
    /*|[A-Za-z]:/*|[A-Za-z]:\\*) ;;
    */*) PY="$(cd "$(dirname "$PY")" && pwd)/$(basename "$PY")" ;;
esac
BUILD="$REPO_ROOT/native/build"
if [ ! -f "$BUILD/drnet.dll" ] || [ ! -f "$BUILD/test_drnet.exe" ]; then
    echo "build first: sh native/build.sh"
    exit 2
fi

case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) native=1 ;;
    *) native=0 ;;
esac
if [ "$native" = 1 ]; then
    where="natively on Windows"
else
    WINE=${WINE:-wine}
    export WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="
    export WINEPREFIX="${WINEPREFIX:-${XDG_CACHE_HOME:-$HOME/.cache}/dronline-test/pair}"
    unset TMPDIR
    mkdir -p "$(dirname "$WINEPREFIX")"
    "$WINE" cmd /c exit >/dev/null 2>&1 || echo "warning: creating the Wine prefix $WINEPREFIX failed"
    where="under $WINE"
fi
if timeout --version >/dev/null 2>&1; then to="timeout 30"; else to=""; fi

out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

# Stage binaries and fixtures in a fresh isolated directory so no adjacent data.win in native/build can
# affect the missing-file test and native/build stays untouched.
stage="$out/bin"
mkdir -p "$stage"
cp "$BUILD/drnet.dll" "$BUILD/test_drnet.exe" "$stage/"
cd "$stage"

# Two distinct synthetic files (> 64 KB so multi-chunk SHA-256 streaming is exercised too).
hashes=$("$PY" - <<'EOF' | tr -d '\r'
import hashlib
a = b"DELTARUNE_TEST_A_" * 5000
b = b"DELTARUNE_TEST_B_" * 5000
open("test_a.win", "wb").write(a)
open("test_b.win", "wb").write(b)
print(hashlib.sha256(a).hexdigest()[:12], hashlib.sha256(b).hexdigest()[:12])
EOF
)
# shellcheck disable=SC2086
set -- $hashes
ha=$1
hb=$2

run_exe() {
    outfile=$1
    shift
    if [ "$native" = 1 ]; then
        $to ./test_drnet.exe "$@" > "$outfile" 2>&1 &
    else
        $to "$WINE" test_drnet.exe "$@" > "$outfile" 2>&1 &
    fi
}

# Prompt refusal bound: well below the 30 s connect timeout of test_drnet (drnet_connect_poll), tolerant of Wine/CI jitter.
MAX_REFUSAL_S=15

check_sync() {
    label=$1
    shift
    first=""
    for f in "$@"; do
        line=$(grep 'final sum' "$f" || true)
        if [ -z "$line" ]; then
            echo "FAIL: $label did not print final sum ($f):"
            cat "$f"
            exit 1
        fi
        sum=$(echo "$line" | sed -E 's/.*final sum ([0-9a-f]+).*/\1/')
        desync=$(echo "$line" | sed -E 's/.*desync (-?[0-9]+).*/\1/')
        if [ -z "$sum" ] || [ "$desync" != "-1" ]; then
            echo "FAIL: $label bad checksum or desync ($sum / $desync in $f)"
            exit 1
        fi
        if [ -z "$first" ]; then
            first=$sum
        elif [ "$sum" != "$first" ]; then
            echo "FAIL: $label checksums differ ($first vs $sum)"
            exit 1
        fi
    done
    LAST_SUM=$first
}

echo "== handshake checks ($where)"

# 1. Matching data.win files connect, stay in sync, and report matching final checksums with no desync
run_exe "$out/m1.txt" host 2 1 30 -game test_a.win
p1=$!
sleep 1
run_exe "$out/m2.txt" client 2 2 30 -game test_a.win
p2=$!
if ! wait "$p1"; then echo "FAIL: matching host failed:"; cat "$out/m1.txt"; exit 1; fi
if ! wait "$p2"; then echo "FAIL: matching client failed:"; cat "$out/m2.txt"; exit 1; fi
check_sync "matching run" "$out/m1.txt" "$out/m2.txt"
echo "  OK: matching data.win ($ha...) connected (sum $LAST_SUM, desync -1)"

# 2. Mismatched data.win client is refused promptly with both SHA-256 prefixes; host stays up for a matching client
run_exe "$out/d1.txt" host 2 1 30 -game test_a.win
p1=$!
sleep 1
t0=$(date +%s)
run_exe "$out/d2.txt" client 2 2 30 -game test_b.win
p2=$!
r2=0; wait "$p2" || r2=$?
elapsed=$(($(date +%s) - t0))
if [ "$r2" -eq 0 ]; then
    echo "FAIL: mismatched data.win client unexpectedly succeeded"; kill "$p1" 2>/dev/null || true; exit 1
fi
if [ "$elapsed" -ge "$MAX_REFUSAL_S" ]; then
    echo "FAIL: mismatched data.win refusal took ${elapsed}s (limit ${MAX_REFUSAL_S}s)"; kill "$p1" 2>/dev/null || true; exit 1
fi
if ! grep -q "different data.win" "$out/d2.txt" || ! grep -q "$ha" "$out/d2.txt" || ! grep -q "$hb" "$out/d2.txt"; then
    echo "FAIL: client missing expected data.win mismatch message ($ha / $hb):"; cat "$out/d2.txt"; kill "$p1" 2>/dev/null || true; exit 1
fi
# Now a matching client joins the same host without restarting it
run_exe "$out/d3.txt" client 2 2 30 -game test_a.win
p3=$!
if ! wait "$p1"; then echo "FAIL: host did not survive rejected client:"; cat "$out/d1.txt"; exit 1; fi
if ! wait "$p3"; then echo "FAIL: matching client after rejection failed:"; cat "$out/d3.txt"; exit 1; fi
check_sync "hash-mismatch recovery" "$out/d1.txt" "$out/d3.txt"
echo "  OK: mismatched data.win ($ha... vs $hb...) refused promptly (${elapsed}s); host accepted matching follow-up client (sum $LAST_SUM)"

# 3. Missing data.win (no -game and no data.win next to drnet.dll) fails immediately on start even when both lack it
t0=$(date +%s)
run_exe "$out/z1.txt" host 2 1 30
p1=$!
run_exe "$out/z2.txt" client 2 2 30
p2=$!
r1=0; wait "$p1" || r1=$?
r2=0; wait "$p2" || r2=$?
elapsed=$(($(date +%s) - t0))
if [ "$r1" -eq 0 ] || [ "$r2" -eq 0 ]; then
    echo "FAIL: missing data.win unexpectedly succeeded ($r1, $r2)"; exit 1
fi
if [ "$elapsed" -ge "$MAX_REFUSAL_S" ]; then
    echo "FAIL: missing data.win failure took ${elapsed}s (limit ${MAX_REFUSAL_S}s)"; exit 1
fi
if ! grep -q "cannot read data.win" "$out/z1.txt" || ! grep -q "cannot read data.win" "$out/z2.txt"; then
    echo "FAIL: missing data.win did not produce expected error:"; cat "$out/z1.txt"; cat "$out/z2.txt"; exit 1
fi
echo "  OK: missing data.win refused immediately on both sides (${elapsed}s)"

# 4. Mismatched player count client is refused promptly; host stays up for a matching client
run_exe "$out/p1.txt" host 2 1 30 -game test_a.win
p1=$!
sleep 1
t0=$(date +%s)
run_exe "$out/p2.txt" client 3 2 30 -game test_a.win
p2=$!
r2=0; wait "$p2" || r2=$?
elapsed=$(($(date +%s) - t0))
if [ "$r2" -eq 0 ]; then
    echo "FAIL: mismatched player count client unexpectedly succeeded"; kill "$p1" 2>/dev/null || true; exit 1
fi
if [ "$elapsed" -ge "$MAX_REFUSAL_S" ]; then
    echo "FAIL: player count refusal took ${elapsed}s (limit ${MAX_REFUSAL_S}s)"; kill "$p1" 2>/dev/null || true; exit 1
fi
if ! grep -q "different player count" "$out/p2.txt"; then
    echo "FAIL: client missing player count mismatch error:"; cat "$out/p2.txt"; kill "$p1" 2>/dev/null || true; exit 1
fi
run_exe "$out/p3.txt" client 2 2 30 -game test_a.win
p3=$!
if ! wait "$p1"; then echo "FAIL: host did not survive player-count mismatch:"; cat "$out/p1.txt"; exit 1; fi
if ! wait "$p3"; then echo "FAIL: matching follow-up client failed:"; cat "$out/p3.txt"; exit 1; fi
check_sync "player-count recovery" "$out/p1.txt" "$out/p3.txt"
echo "  OK: mismatched player count refused promptly (${elapsed}s); host accepted matching follow-up client (sum $LAST_SUM)"

# 5. Three players: mismatched player 3 is refused promptly without aborting the host or waiting player 2
run_exe "$out/t1.txt" host 3 1 30 -game test_a.win
p1=$!
sleep 1
run_exe "$out/t2.txt" client 3 2 30 -game test_a.win
p2=$!
sleep 1
t0=$(date +%s)
run_exe "$out/t3.txt" client 3 3 30 -game test_b.win
p3=$!
r3=0; wait "$p3" || r3=$?
elapsed=$(($(date +%s) - t0))
if [ "$r3" -eq 0 ]; then
    echo "FAIL: mismatched player 3 unexpectedly succeeded"; kill "$p1" "$p2" 2>/dev/null || true; exit 1
fi
if [ "$elapsed" -ge "$MAX_REFUSAL_S" ]; then
    echo "FAIL: 3-player mismatch refusal took ${elapsed}s (limit ${MAX_REFUSAL_S}s)"; kill "$p1" "$p2" 2>/dev/null || true; exit 1
fi
if ! grep -q "different data.win" "$out/t3.txt" || ! grep -q "$ha" "$out/t3.txt" || ! grep -q "$hb" "$out/t3.txt"; then
    echo "FAIL: player 3 missing data.win mismatch error:"; cat "$out/t3.txt"; kill "$p1" "$p2" 2>/dev/null || true; exit 1
fi
# Matching player 3 now joins the waiting host and player 2
run_exe "$out/t4.txt" client 3 3 30 -game test_a.win
p4=$!
if ! wait "$p1"; then echo "FAIL: 3-player host failed after rejecting bad player 3:"; cat "$out/t1.txt"; exit 1; fi
if ! wait "$p2"; then echo "FAIL: compatible player 2 failed after bad player 3 was rejected:"; cat "$out/t2.txt"; exit 1; fi
if ! wait "$p4"; then echo "FAIL: matching player 3 failed:"; cat "$out/t4.txt"; exit 1; fi
check_sync "3-player recovery" "$out/t1.txt" "$out/t2.txt" "$out/t4.txt"
echo "  OK: 3-player mismatched peer refused promptly (${elapsed}s) without aborting host or player 2 (sum $LAST_SUM)"

# 6 and 7. Three players: player 2 says HELLO, then leaves before player 3 arrives (issue #58). The host must stop
# counting it. Otherwise it welcomes player 3 at once, and the host and player 3 wait for player 2's input until the
# 10 s exchange timeout, while the player 2 who comes back later is never let in. Here a new player 2 completes the
# session, and all three must end in sync.
#   lobby_quit <mode> <s>: player 3 starts <s> seconds after player 2 is gone. Mode "lobby" leaves with a goodbye
#   (drnet_bye, as when the window is closed), and 0.5 s is well within the 2 s the host gives a silent client, so
#   only the goodbye can make it stop counting player 2; "lobby-crash" exits without a word (a killed game), and 3 s
#   is after the host has given up on player 2.
lobby_quit() {
    mode=$1
    gap=$2
    run_exe "$out/$mode-1.txt" host 3 1 30 -game test_a.win
    q1=$!
    sleep 1
    run_exe "$out/$mode-2.txt" client 3 2 30 0 -1 127.0.0.1 "$mode" -game test_a.win
    q2=$!
    r2=0; wait "$q2" || r2=$?
    if [ "$r2" -ne 0 ] || ! grep -q "the lobby before everybody is there" "$out/$mode-2.txt"; then
        echo "FAIL: $mode: player 2 did not leave the lobby as planned:"; cat "$out/$mode-2.txt"; kill "$q1" 2>/dev/null || true; exit 1
    fi
    sleep "$gap"
    run_exe "$out/$mode-3.txt" client 3 3 30 -game test_a.win
    q3=$!
    # with the bug, the host and player 3 start now and give up 10 s later
    sleep 3
    run_exe "$out/$mode-4.txt" client 3 2 30 -game test_a.win
    q4=$!
    if ! wait "$q1"; then echo "FAIL: $mode: host failed after player 2 left the lobby:"; cat "$out/$mode-1.txt"; kill "$q3" "$q4" 2>/dev/null || true; exit 1; fi
    if ! wait "$q3"; then echo "FAIL: $mode: player 3 failed after player 2 left the lobby:"; cat "$out/$mode-3.txt"; kill "$q4" 2>/dev/null || true; exit 1; fi
    if ! wait "$q4"; then echo "FAIL: $mode: the new player 2 failed:"; cat "$out/$mode-4.txt"; exit 1; fi
    check_sync "$mode" "$out/$mode-1.txt" "$out/$mode-3.txt" "$out/$mode-4.txt"
    # the waiting screen's count (drnet_lobby) went 1, 2 (player 2), 1 (player 2 gone), 2 (player 3)
    counts=$(tr -d '\r' < "$out/$mode-1.txt" | sed -n 's/^lobby: \([0-9]\) of 3 players here$/\1/p' | tr -d '\n')
    if [ "$counts" != "1212" ]; then
        echo "FAIL: $mode: the host's player count went $counts, expected 1212:"; cat "$out/$mode-1.txt"; exit 1
    fi
}
lobby_quit lobby 0.5
echo "  OK: 3 players, player 2 said goodbye in the lobby: the host waited for a new player 2 (sum $LAST_SUM)"
lobby_quit lobby-crash 3
echo "  OK: 3 players, player 2 was killed in the lobby: the host stopped counting it, waited for a new one (sum $LAST_SUM)"
echo "OK: all handshake checks passed"

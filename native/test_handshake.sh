#!/bin/sh
# Handshake checks for drnet.dll without the game: verifies that matching data.win files connect,
# mismatched data.win files are refused immediately with both SHA-256 prefixes (verified against Python's
# hashlib.sha256), one missing data.win is refused, and mismatched player counts are refused.
# Runs natively on Windows (Git Bash) or under Wine on Linux. Used by tools/verify.sh and CI.
set -eu
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$(sh "$REPO_ROOT/tools/find_python.sh")"
cd "$REPO_ROOT/native/build"
[ -f drnet.dll ] && [ -f test_drnet.exe ] || { echo "build first: sh native/build.sh"; exit 2; }

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
cleanup() {
    rm -rf "$out" _test_data_a.win _test_data_b.win
}
trap cleanup EXIT

# Two distinct synthetic files (> 64 KB so multi-chunk SHA-256 streaming is exercised too).
"$PY" - "$out/hashes.txt" <<'EOF'
import hashlib, sys
a = b"DELTARUNE_TEST_A_" * 5000
b = b"DELTARUNE_TEST_B_" * 5000
open("_test_data_a.win", "wb").write(a)
open("_test_data_b.win", "wb").write(b)
open(sys.argv[1], "w").write(hashlib.sha256(a).hexdigest()[:12] + " " + hashlib.sha256(b).hexdigest()[:12] + "\n")
EOF
read -r ha hb < "$out/hashes.txt"

run_exe() {
    outfile=$1
    shift
    if [ "$native" = 1 ]; then
        $to ./test_drnet.exe "$@" > "$outfile" 2>&1 &
    else
        $to "$WINE" test_drnet.exe "$@" > "$outfile" 2>&1 &
    fi
}

echo "== handshake checks ($where)"

# 1. Matching data.win files connect and stay in sync
run_exe "$out/m1.txt" host 2 1 30 -game _test_data_a.win
p1=$!
sleep 1
run_exe "$out/m2.txt" client 2 2 30 -game _test_data_a.win
p2=$!
wait "$p1" || { echo "FAIL: matching host failed:"; cat "$out/m1.txt"; exit 1; }
wait "$p2" || { echo "FAIL: matching client failed:"; cat "$out/m2.txt"; exit 1; }
grep -q "final sum" "$out/m1.txt" && grep -q "final sum" "$out/m2.txt" || { echo "FAIL: matching run did not finish"; exit 1; }
echo "  OK: matching data.win ($ha...) connected"

# 2. Mismatched data.win files are refused immediately with both SHA-256 prefixes
run_exe "$out/d1.txt" host 2 1 30 -game _test_data_a.win
p1=$!
sleep 1
run_exe "$out/d2.txt" client 2 2 30 -game _test_data_b.win
p2=$!
r1=0; wait "$p1" || r1=$?
r2=0; wait "$p2" || r2=$?
[ "$r1" -ne 0 ] && [ "$r2" -ne 0 ] || { echo "FAIL: mismatched data.win unexpectedly succeeded ($r1, $r2)"; exit 1; }
grep -q "different data.win" "$out/d1.txt" && grep -q "$ha" "$out/d1.txt" && grep -q "$hb" "$out/d1.txt" || {
    echo "FAIL: host missing expected data.win mismatch message ($ha / $hb):"; cat "$out/d1.txt"; exit 1;
}
grep -q "different data.win" "$out/d2.txt" && grep -q "$ha" "$out/d2.txt" && grep -q "$hb" "$out/d2.txt" || {
    echo "FAIL: client missing expected data.win mismatch message ($ha / $hb):"; cat "$out/d2.txt"; exit 1;
}
echo "  OK: mismatched data.win ($ha... vs $hb...) refused on both sides"

# 3. Missing data.win on one side is refused
run_exe "$out/z1.txt" host 2 1 30 -game _test_data_a.win
p1=$!
sleep 1
run_exe "$out/z2.txt" client 2 2 30
p2=$!
r1=0; wait "$p1" || r1=$?
r2=0; wait "$p2" || r2=$?
[ "$r1" -ne 0 ] && [ "$r2" -ne 0 ] || { echo "FAIL: missing data.win on client unexpectedly succeeded"; exit 1; }
grep -q "different data.win" "$out/z1.txt" && grep -q "different data.win" "$out/z2.txt" || {
    echo "FAIL: missing data.win did not produce expected error:"; cat "$out/z1.txt"; cat "$out/z2.txt"; exit 1;
}
echo "  OK: one-sided missing data.win refused"

# 4. Mismatched player count is refused
run_exe "$out/p1.txt" host 2 1 30 -game _test_data_a.win
p1=$!
sleep 1
run_exe "$out/p2.txt" client 3 2 30 -game _test_data_a.win
p2=$!
r1=0; wait "$p1" || r1=$?
r2=0; wait "$p2" || r2=$?
[ "$r1" -ne 0 ] && [ "$r2" -ne 0 ] || { echo "FAIL: mismatched player count unexpectedly succeeded"; exit 1; }
grep -q "players=" "$out/p1.txt" && grep -q "different player count" "$out/p2.txt" || {
    echo "FAIL: player count mismatch did not produce expected error:"; cat "$out/p1.txt"; cat "$out/p2.txt"; exit 1;
}
echo "  OK: mismatched player count refused on both sides"
echo "OK: all handshake checks passed"

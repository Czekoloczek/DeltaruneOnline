#!/bin/sh
# Runs a host and a client on one PC (Linux) to test online play without a second machine.
# Each instance gets its own prefix with a copy of the same save folder and its own dronline.ini.
# Keyboard input goes to the focused window: focus the host window to play P1, the client window for P2.
# The copied saves are switched to windowed mode, so both windows fit on one screen.
#
# Usage: tools/local_test.sh "<game folder with the patched chapter>" "<save folder to copy>" [players]
#   game folder: contains DELTARUNE.exe; chapterN_windows/ has the patched data.win and drnet.dll
#   save folder: e.g. <prefix>/drive_c/users/steamuser/AppData/Local/DELTARUNE
#
# Runner: Proton (like the real game) if DRONLINE_PROTON points to a Proton folder or GE-Proton is found
# in ~/.local/share/Steam/compatibilitytools.d; otherwise plain Wine. DRONLINE_RUNNER=wine forces Wine.
# DRONLINE_LAG=40 adds 40 ms of latency to every packet each side sends (ping about 80 ms) to test a slow
# connection on one PC; DRONLINE_DELAY=auto|<frames> sets the input delay (default auto).
set -e
GAME="$1"; SAVES="$2"; PLAYERS="${3:-2}"
[ -f "$GAME/DELTARUNE.exe" ] || { echo "no DELTARUNE.exe in $GAME"; exit 1; }
[ -d "$SAVES" ] || { echo "no save folder $SAVES"; exit 1; }
BASE="${XDG_CACHE_HOME:-$HOME/.cache}/dronline-test"
STEAM_ROOT="${STEAM_ROOT:-$HOME/.local/share/Steam}"
PROTON="${DRONLINE_PROTON:-$(ls -d "$STEAM_ROOT"/compatibilitytools.d/GE-Proton* 2>/dev/null | sort -V | tail -1)}"
RUNNER="${DRONLINE_RUNNER:-$([ -x "$PROTON/proton" ] && echo proton || echo wine)}"
echo "runner: $RUNNER ${PROTON:+($PROTON)}"

start_one() {   # name mode player
    PFX="$BASE/$RUNNER-$1"
    mkdir -p "$PFX"
    if [ "$RUNNER" = proton ]; then
        export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM_ROOT" STEAM_COMPAT_DATA_PATH="$PFX"
        [ -d "$PFX/pfx/drive_c" ] || "$PROTON/proton" run cmd /c exit >/dev/null 2>&1
        DST="$PFX/pfx/drive_c/users/steamuser/AppData/Local/DELTARUNE"
    else
        [ -d "$PFX/drive_c" ] || WINEPREFIX="$PFX" WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml=" wineboot -u >/dev/null 2>&1
        DST="$PFX/drive_c/users/$USER/AppData/Local/DELTARUNE"
    fi
    rm -rf "$DST"; mkdir -p "$DST"; cp -a "$SAVES"/. "$DST"/
    [ -f "$DST/true_config.ini" ] && sed -i 's/^FULLSCREEN=.*/FULLSCREEN="0.000000"\r/' "$DST/true_config.ini"
    printf '[online]\nmode=%s\nplayer=%s\nplayers=%s\nhost=127.0.0.1\nport=7845\ndelay=%s\nlag=%s\nseed=1234\ndebug=1\n' "$2" "$3" "$PLAYERS" "${DRONLINE_DELAY:-auto}" "${DRONLINE_LAG:-0}" > "$DST/dronline.ini"
    echo "$1: $DST"
    if [ "$RUNNER" = proton ]; then
        (cd "$GAME" && DRONLINE_ROLE="$1" "$PROTON/proton" run "$GAME/DELTARUNE.exe" > "$BASE/$1.log" 2>&1 &)
    else
        (cd "$GAME" && DRONLINE_ROLE="$1" WINEPREFIX="$PFX" WINEDEBUG=-all wine DELTARUNE.exe > "$BASE/$1.log" 2>&1 &)
    fi
}
start_one host host 1
sleep 3
start_one client client 2
if [ "$PLAYERS" -ge 3 ]; then sleep 3; start_one client2 client 3; fi
echo "logs in $BASE/*.log (game) and <save folder>/drnet.log (network); choose the same chapter and slot in every window"

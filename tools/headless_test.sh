#!/bin/sh
# Starts a host and a client of the patched chapter directly (no launcher) inside invisible
# gamescope instances (--backend headless), lets them run for SECONDS, stops them and prints
# both drnet.log files. Uses the Proton prefixes and save copies prepared by tools/local_test.sh, so it needs
# Proton too: GE-Proton in ~/.local/share/Steam/compatibilitytools.d, or DRONLINE_PROTON=<Proton folder>.
#
# Usage: tools/headless_test.sh "<game folder>" [chapter=5] [seconds=40]
# HOST_RES / CLIENT_RES="2560x1440": screen size of each side (default 1280x960).
# HOST_KEYS="10:z 13:z 16:z": press keys in the host game at those seconds after start (xdotool).
# The save folders get debug=1, so each side also writes dronline_state.txt (instances per checkpoint).
# LAG=40: 40 ms of extra latency per packet on each side (ping about 80 ms). DELAY=auto|<frames> (default auto).
# HOST_SCRIPT / CLIENT_SCRIPT=<file.txt|file.bin>: that side plays this input script instead of the keyboard
# (tools/input_script.py; a .txt is compiled first). SCRIPT_END=idle|keys|quit: what follows its last frame.
# Each side starts with the "launcher" parameter, as the chapter select passes it: without it the chapter
# looks for its music in chapter<N>_windows/mus/, and the Chapter 5 logo waits for its music forever.
set -e
GAME="$1"; CH="${2:-5}"; SECS="${3:-40}"
BASE="${XDG_CACHE_HOME:-$HOME/.cache}/dronline-test"
STEAM_ROOT="${STEAM_ROOT:-$HOME/.local/share/Steam}"
PROTON="${DRONLINE_PROTON:-$(ls -d "$STEAM_ROOT"/compatibilitytools.d/GE-Proton* 2>/dev/null | sort -V | tail -1)}"
[ -x "$PROTON/proton" ] || { echo "Proton not found: install GE-Proton or set DRONLINE_PROTON=<Proton folder>"; exit 1; }
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM_ROOT"
for r in host client; do
    LOG="$BASE/proton-$r/pfx/drive_c/users/steamuser/AppData/Local/DELTARUNE/drnet.log"
    if [ ! -d "$(dirname "$LOG")" ]; then
        if [ -d "$BASE/wine-$r" ]; then
            echo "only a Wine prefix for $r: this script runs Proton, so run tools/local_test.sh once with Proton"
        else
            echo "run tools/local_test.sh once first (prefix $r missing)"
        fi
        exit 1
    fi
    : > "$LOG"
    D="$(dirname "$LOG")"; rm -f "$D/dronline_state.txt"
    sed -i '/^debug=/d; /^lag=/d; /^delay=/d; /^script=/d; /^script_end=/d' "$D/dronline.ini"
    printf 'debug=1\nlag=%s\ndelay=%s\n' "${LAG:-0}" "${DELAY:-auto}" >> "$D/dronline.ini"
    if [ "$r" = host ]; then SCRIPT="${HOST_SCRIPT:-}"; else SCRIPT="${CLIENT_SCRIPT:-}"; fi
    if [ -n "$SCRIPT" ]; then
        case "$SCRIPT" in
            *.bin) cp "$SCRIPT" "$D/dronline_script.bin" ;;
            *) python3 "$(dirname "$0")/input_script.py" compile "$SCRIPT" "$D/dronline_script.bin" ;;
        esac
        printf 'script=dronline_script.bin\nscript_end=%s\n' "${SCRIPT_END:-idle}" >> "$D/dronline.ini"
    fi
done
start_one() {   # role WxH
    set -- "$1" "${2%x*}" "${2#*x}"
    ( cd "$GAME/chapter${CH}_windows" && STEAM_COMPAT_DATA_PATH="$BASE/proton-$1" DRONLINE_ROLE="$1" \
      gamescope --backend headless -W "$2" -H "$3" -- sh -c 'echo "$DISPLAY" > "$0"; exec "$@"' "$BASE/display-$1" \
      "$PROTON/proton" run "$GAME/DELTARUNE.exe" -game data.win launcher \
      > "$BASE/headless-$1.log" 2>&1 & )
}
start_one host ${HOST_RES:-1280x960}
sleep 2
start_one client ${CLIENT_RES:-1280x960}
echo "running for $SECS s (nothing is shown on screen)..."
T0=$(date +%s)
for k in $HOST_KEYS; do
    at=${k%%:*}; key=${k#*:}
    while [ $(( $(date +%s) - T0 )) -lt "$at" ]; do sleep 0.2; done
    DISPLAY="$(cat "$BASE/display-host" 2>/dev/null)" xdotool key --delay 100 "$key" && echo "  key $key at ${at}s"
done
while [ $(( $(date +%s) - T0 )) -lt "$SECS" ]; do sleep 1; done
for r in host client; do STEAM_COMPAT_DATA_PATH="$BASE/proton-$r" WINEPREFIX="$BASE/proton-$r/pfx" "$PROTON/files/bin/wineserver" -k 2>/dev/null || true; done
pkill -f "gamescope --backend [h]eadless" 2>/dev/null || true
for r in host client; do
    echo "== $r drnet.log"
    cat "$BASE/proton-$r/pfx/drive_c/users/steamuser/AppData/Local/DELTARUNE/drnet.log"
done

#!/bin/sh
# Installs what the checks without the game need (tools/verify.sh): Python 3.11+, mingw-w64 for drnet.dll and Wine
# for the lockstep test. For CI, cloud agents (CodeRabbit, Claude Code on the web) and new PCs.
# Supports apt (Debian, Ubuntu), dnf (Fedora) and pacman (Arch, CachyOS).
# Game and mod files are never needed or downloaded: they must not leave the players' own PCs.
set -eu
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"
# Wine goes in a separate step everywhere: without it only the lockstep test is skipped, so a missing Wine package
# must not stop the rest of the setup.
NO_WINE="Wine not available here: tools/verify.sh will skip the lockstep test"
if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -qq
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
        python3 gcc-mingw-w64-x86-64 xdelta3
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends wine wine64 \
        || echo "$NO_WINE"
elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y python3 mingw64-gcc xdelta
    $SUDO dnf install -y wine-core || echo "$NO_WINE"
elif command -v pacman >/dev/null 2>&1; then
    $SUDO pacman -S --needed --noconfirm python mingw-w64-gcc xdelta3
    $SUDO pacman -S --needed --noconfirm wine || echo "$NO_WINE"
else
    echo "Unknown package manager. Install by hand: Python 3.11+, x86_64-w64-mingw32-gcc, Wine, xdelta3."
    exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is needed, found " + sys.version'
x86_64-w64-mingw32-gcc --version | head -1
command -v wine >/dev/null 2>&1 || command -v wine64 >/dev/null 2>&1 || echo "Wine missing: tools/verify.sh will skip the lockstep test"
echo "setup_env: OK"

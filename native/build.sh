#!/bin/sh
# Builds drnet.dll (64-bit Windows, also runs under Proton) with mingw-w64.
# Arch/CachyOS: pacman -S mingw-w64-gcc   Debian/Ubuntu: apt install gcc-mingw-w64-x86-64
set -e
cd "$(dirname "$0")"
mkdir -p build
# CC=gcc for the mingw-w64 gcc on Windows (Git Bash / MSYS2)
CC=${CC:-x86_64-w64-mingw32-gcc}
"$CC" -O2 -shared -static-libgcc -Wall -o build/drnet.dll drnet.c -lws2_32 -luser32
# Standalone lockstep test (native/test_pair.sh runs it under Wine, or natively on Windows)
"$CC" -O2 -static-libgcc -Wall -o build/test_drnet.exe test_drnet.c
# A stand-in game for the Agent API test (native/test_agent.py)
"$CC" -O2 -static-libgcc -Wall -o build/test_agent.exe test_agent.c
echo "built native/build/drnet.dll, native/build/test_drnet.exe and native/build/test_agent.exe"

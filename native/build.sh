#!/bin/sh
# Builds drnet.dll (64-bit Windows, also runs under Proton) with mingw-w64.
# Arch/CachyOS: pacman -S mingw-w64-gcc   Debian/Ubuntu: apt install gcc-mingw-w64-x86-64
set -e
cd "$(dirname "$0")"
mkdir -p build
x86_64-w64-mingw32-gcc -O2 -shared -static-libgcc -Wall -o build/drnet.dll drnet.c -lws2_32 -luser32
echo "built native/build/drnet.dll"

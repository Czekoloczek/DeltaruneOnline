#!/bin/sh
# Prints the path of the first Python 3.11+ it finds ($PYTHON first, then python3, versioned names, and python), or fails.
# Used by setup_env.sh, verify.sh and native/test_handshake.sh: on Ubuntu 22.04 python3 is 3.10 and 3.11 is a separate
# python3.11; on Windows Python 3.11+ is often installed only as python.exe.
for p in "${PYTHON:-}" python3 python3.15 python3.14 python3.13 python3.12 python3.11 python; do
    [ -n "$p" ] || continue
    command -v "$p" >/dev/null 2>&1 || continue
    if "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        command -v "$p"
        exit 0
    fi
done
echo "No Python 3.11 or newer found: set PYTHON=/path/to/python3.x or run tools/setup_env.sh" >&2
exit 1

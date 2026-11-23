#!/bin/sh
# Everything that can be checked without the game files, in one command (tools/setup_env.sh installs the tools):
#   1. unit tests of the patcher and the cross-file consistency checks,
#   2. building drnet.dll, test_drnet.exe and test_agent.exe,
#   3. the lockstep test: host and clients must receive identical input (Wine on Linux, native on Windows/Git Bash),
#   4. the Agent API test: a stand-in game loads drnet.dll and an agent talks to it (native/test_agent.py).
# SKIP_LOCKSTEP=1 skips steps 3 and 4. The game itself needs the players' own files and is tested on their PCs.
set -eu
cd "$(dirname "$0")/.."
PY="$(sh tools/find_python.sh)"
"$PY" -m unittest discover -s tests -v
sh native/build.sh
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) runner=native ;;
    *) runner="${WINE:-$(command -v wine || command -v wine64 || true)}" ;;
esac
if [ "${SKIP_LOCKSTEP:-0}" = 1 ] || [ -z "$runner" ]; then
    echo "verify: skipping the lockstep and Agent API tests (SKIP_LOCKSTEP=1 or no Wine)"
else
    [ "$runner" = native ] || export WINE="$runner"
    for args in "0 450 2" "30 450 2" "0 450 3" "0 150 3 12"; do
        # shellcheck disable=SC2086
        sh native/test_pair.sh $args
    done
    "$PY" native/test_agent.py
fi
echo "verify: OK"

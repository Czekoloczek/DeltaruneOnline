#!/usr/bin/env python3
"""Build the DeltaruneOnline test version of Chapter 5 from your own files.

Steps:
  1. check that your Chapter 5 data.win is the version the Local Multiplayer mod expects,
  2. apply your copy of Yomsman's Local Multiplayer v19 patch (xdelta3),
  3. run our UndertaleModTool script that adds the network test (patcher/ch5/net_test.csx),
  4. write the result to <out>/chapter5_windows/data.win (your game folder is never changed).

Example (Windows, in a terminal opened in the repository folder):
  py patcher\\build.py --game "C:\\Program Files (x86)\\Steam\\steamapps\\common\\DELTARUNE" ^
      --mod "C:\\Downloads\\deltarunelocalmultiplayer_v19" ^
      --utmt "C:\\Tools\\UTMT_CLI\\UndertaleModCli.exe" --xdelta3 "C:\\Tools\\xdelta3.exe" --out build

Only the Python standard library is needed. xdelta3 and the UndertaleModTool CLI must be installed separately.
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# SHA-256 of the files we know how to patch.
CH5_VANILLA_SHA256 = "370dfd141d2955d5a1960122919b16e4092b52ffbb85fda541bc4680c6b3b85c"  # DELTARUNE 1.05 (Steam)
CH5_LOCALMP_SHA256 = "2a700455ab74f2c4dcd84a57409926dd90b3f1000140ae526c0ac3395bc3f28b"  # + Local Multiplayer v19
CH5_MOD_PATCH_GLOB = "Chapter5LocalMultiplayer*.xdelta"
CH5_SCRIPT = REPO / "patcher" / "ch5" / "net_test.csx"


def fail(message: str) -> None:
    print(f"\nERROR: {message}", file=sys.stderr)
    sys.exit(1)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def find_tool(given: str | None, names: list[str], what: str) -> list[str]:
    """Return the command prefix for a tool (a .dll is started with `dotnet`)."""
    if given:
        path = Path(given)
        if not path.exists():
            fail(f"{what} not found at {path}")
        if path.suffix.lower() == ".dll":
            dotnet = shutil.which("dotnet")
            if not dotnet:
                fail("`dotnet` is needed to run a .dll. Install the .NET SDK or pass the .exe instead.")
            return [dotnet, str(path)]
        return [str(path)]
    for name in names:
        found = shutil.which(name)
        if found:
            return [found]
    fail(f"{what} not found. Pass its path with the option shown in --help.")
    return []


def run(cmd: list[str], what: str) -> None:
    print(f"  > {' '.join(cmd)}")
    # stdin=DEVNULL: the UndertaleModTool CLI waits for keyboard input otherwise.
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-4000:])
        print(result.stderr[-4000:], file=sys.stderr)
        fail(f"{what} failed (exit code {result.returncode}).")
    for line in result.stdout.splitlines():
        if line.strip() and "Writing chunk" not in line:
            print(f"    {line}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", required=True, help="DELTARUNE folder (the one with DELTARUNE.exe), not changed")
    parser.add_argument("--mod", required=True, help="Local Multiplayer v19 folder or its Chapter 5 .xdelta file")
    parser.add_argument("--utmt", help="UndertaleModTool CLI (UndertaleModCli.exe or UndertaleModCli.dll)")
    parser.add_argument("--xdelta3", help="xdelta3 program (default: search PATH)")
    parser.add_argument("--out", default="build", help="output folder (default: build)")
    parser.add_argument("--make-patch", action="store_true",
                        help="also write an .xdelta patch (original Chapter 5 -> online test) for a friend "
                             "who has their own copy of the game")
    args = parser.parse_args()

    game = Path(args.game)
    vanilla = game / "chapter5_windows" / "data.win"
    if not vanilla.exists():
        fail(f"{vanilla} not found. --game must be the DELTARUNE folder.")

    mod = Path(args.mod)
    if mod.is_dir():
        patches = sorted(mod.glob(CH5_MOD_PATCH_GLOB))
        if not patches:
            fail(f"no {CH5_MOD_PATCH_GLOB} in {mod}")
        mod_patch = patches[-1]
    else:
        mod_patch = mod
    if not mod_patch.exists():
        fail(f"{mod_patch} not found")

    xdelta = find_tool(args.xdelta3, ["xdelta3", "xdelta3.exe", "xdelta"], "xdelta3")
    utmt = find_tool(args.utmt, ["UndertaleModCli", "UndertaleModCli.exe"], "UndertaleModTool CLI (--utmt)")

    out_dir = Path(args.out) / "chapter5_windows"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "data.win"

    print("1/3 Checking your Chapter 5 data.win ...")
    got = sha256(vanilla)
    if got != CH5_VANILLA_SHA256:
        fail("your Chapter 5 data.win is not the version this patch was made for (DELTARUNE 1.05).\n"
             f"       expected {CH5_VANILLA_SHA256}\n       found    {got}\n"
             "       If you already applied a mod to it, use an unmodified copy (Steam: Verify integrity of game files).")

    with tempfile.TemporaryDirectory(prefix="dro_build_") as tmp:
        localmp = Path(tmp) / "ch5_localmp.win"
        print("2/3 Applying Local Multiplayer v19 ...")
        run(xdelta + ["-d", "-f", "-s", str(vanilla), str(mod_patch), str(localmp)], "xdelta3")
        got = sha256(localmp)
        if got != CH5_LOCALMP_SHA256:
            fail(f"after applying {mod_patch.name} the file is not Local Multiplayer v19 for Chapter 5 (sha256 {got}).")

        print("3/3 Adding the DeltaruneOnline network test ...")
        run(utmt + ["load", str(localmp), "-s", str(CH5_SCRIPT), "-o", str(out_file), "-f"], "UndertaleModTool CLI")

    print(f"\nDone: {out_file}  (sha256 {sha256(out_file)})")

    if args.make_patch:
        patch = Path(args.out) / "DeltaruneOnline_ch5_test.xdelta"
        print("Writing a patch for friends ...")
        run(xdelta + ["-e", "-9", "-f", "-s", str(vanilla), str(out_file), str(patch)], "xdelta3")
        print(f"Patch: {patch} ({patch.stat().st_size // 1024} KB). It applies to the original Chapter 5 data.win.")

    print("\nCopy chapter5_windows/data.win into a COPY of your DELTARUNE folder (never your Steam folder).")


if __name__ == "__main__":
    main()

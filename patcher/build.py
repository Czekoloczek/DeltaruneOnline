#!/usr/bin/env python3
"""Build DeltaruneOnline for Chapter 5 from your own files, with version checks.

Lockstep only works if every player runs a byte-identical data.win. This script makes that easy to check:
  1. checks the SHA-256 of your original Chapter 5 data.win (must be a version we know),
  2. applies your own copy of Yomsman's Local Multiplayer patch (xdelta3) and checks the result,
  3. runs patcher/dronline_patch.py on it,
  4. prints the SHA-256 of the final data.win: compare it with your partner's before playing.
Your game folder is never changed; the result goes to <out>/chapter5_windows/.

If you already have a modded data.win (for example a port of the mod to a newer game patch), pass it with
--modded instead of --game/--mod. Then step 1 and 2 are skipped and only the hashes are printed.

Example (Windows):
  py patcher\\build.py --game "C:\\Program Files (x86)\\Steam\\steamapps\\common\\DELTARUNE" ^
      --mod "C:\\Downloads\\deltarunelocalmultiplayer_v19" --utmt "C:\\Tools\\UTMT_CLI\\UndertaleModCli.exe" ^
      --xdelta3 "C:\\Tools\\xdelta3.exe" --out build
(--xdelta3 also takes just a program name on PATH, such as xdelta3.)
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Chapter 5 versions we know: original data.win -> (description, mod patch file pattern, SHA-256 after the mod).
KNOWN_CH5 = {
    "370dfd141d2955d5a1960122919b16e4092b52ffbb85fda541bc4680c6b3b85c": (
        "DELTARUNE 1.05 (Steam), Local Multiplayer v19",
        "Chapter5LocalMultiplayer*.xdelta",
        "2a700455ab74f2c4dcd84a57409926dd90b3f1000140ae526c0ac3395bc3f28b",
    ),
}


def fail(message: str) -> None:
    print(f"\nERROR: {message}", file=sys.stderr)
    sys.exit(1)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def find_xdelta(given):
    if given:
        # a path to the program, or just its name when it is on PATH (e.g. --xdelta3 xdelta3)
        found = given if Path(given).is_file() else shutil.which(given)
        if not found:
            fail(f"xdelta3 not found: {given} is neither a file nor a program on PATH")
        return found
    for name in ("xdelta3", "xdelta3.exe", "xdelta"):
        found = shutil.which(name)
        if found:
            return found
    fail("xdelta3 not found. Install it or pass --xdelta3.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", help="DELTARUNE folder (the one with DELTARUNE.exe); not changed")
    ap.add_argument("--mod", help="Local Multiplayer folder or its Chapter 5 .xdelta file")
    ap.add_argument("--modded", help="an already modded Chapter 5 data.win, instead of --game and --mod")
    ap.add_argument("--utmt", required=True, help="UndertaleModCli.exe, or UndertaleModCli.dll (run with dotnet)")
    ap.add_argument("--xdelta3", help="xdelta3 program: a path, or a name on PATH (default: search PATH)")
    ap.add_argument("--dll", help="drnet.dll (default: native/build/drnet.dll, see dronline_patch.py)")
    ap.add_argument("--out", default="build", help="output folder (default: build)")
    a = ap.parse_args()

    out_dir = Path(a.out) / "chapter5_windows"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "data.win"

    with tempfile.TemporaryDirectory(prefix="dro_build_") as tmp:
        if a.modded:
            modded = Path(a.modded)
            if not modded.is_file():
                fail(f"{modded} not found")
            print("1/3 Using your modded data.win as it is (no version check)")
            print(f"    {modded}  sha256 {sha256(modded)}")
        else:
            if not (a.game and a.mod):
                fail("pass --game and --mod, or --modded")
            vanilla = Path(a.game) / "chapter5_windows" / "data.win"
            if not vanilla.is_file():
                fail(f"{vanilla} not found. --game must be the DELTARUNE folder.")
            print("1/3 Checking your Chapter 5 data.win ...")
            got = sha256(vanilla)
            if got not in KNOWN_CH5:
                fail(f"unknown Chapter 5 version (sha256 {got}).\n"
                     "       Known: " + ", ".join(f"{d} ({h[:12]}...)" for h, (d, _, _) in KNOWN_CH5.items()) + ".\n"
                     "       If you use a mod port for this version, build with --modded, and make sure your\n"
                     "       partner ends up with the same final sha256.")
            desc, pattern, modded_sha = KNOWN_CH5[got]
            print(f"    {desc}")
            mod = Path(a.mod)
            patches = sorted(mod.glob(pattern)) if mod.is_dir() else [mod]
            if not patches or not patches[-1].is_file():
                fail(f"no {pattern} found in {mod}")
            modded = Path(tmp) / "ch5_modded.win"
            print(f"2/3 Applying {patches[-1].name} ...")
            cmd = [find_xdelta(a.xdelta3), "-d", "-f", "-s", str(vanilla), str(patches[-1]), str(modded)]
            res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
            if res.returncode != 0:
                fail(f"xdelta3 failed: {res.stderr.strip()}")
            got = sha256(modded)
            if got != modded_sha:
                fail(f"after applying {patches[-1].name} the file is not the expected mod version (sha256 {got}).")

        print("3/3 Running dronline_patch.py ...")
        cmd = [sys.executable, str(HERE / "dronline_patch.py"), "--utmt", a.utmt, "--data", str(modded),
               "--out", str(out_file)]
        if a.dll:
            cmd += ["--dll", a.dll]
        if subprocess.run(cmd, stdin=subprocess.DEVNULL).returncode != 0:
            fail("dronline_patch.py failed")

    print(f"\nDone: {out_file}")
    print(f"sha256 {sha256(out_file)}")
    print("Compare this sha256 with your partner's. If they differ, your games will not stay in sync.")
    print("Copy chapter5_windows/ (data.win + drnet.dll) into a COPY of your DELTARUNE folder, never the Steam folder")
    print("(README.md, Usage).")


if __name__ == "__main__":
    main()

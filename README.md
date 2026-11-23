# DeltaruneOnline

Online multiplayer for DELTARUNE, built on top of the [Local Multiplayer mod](https://gamebanana.com/mods/601376) by Yomsman.
Every player runs the game on their own PC, and the games stay in sync by exchanging only controller input each frame.

> **Status:** Chapter 5 runs in lockstep between two PCs over the internet (a Windows host and a Linux client under
> Proton, about 14 ms ping): over 5 minutes on the file menu without a desync, and when one player quits, the other
> game shows it within 50 ms. Not tested yet: battles, a chapter change, and people actually playing.

> **Vibecoded:** this project is made with AI (Claude): code, analysis and documentation.

## How it works

- **You build it locally.** The patcher takes files you already have: your copy of DELTARUNE and the Local Multiplayer mod
  downloaded from GameBanana. It applies the changes on your machine. No game or mod files are distributed here.
- **Input lockstep.** All players run the full game from the same save. Each frame, every game sends the pressed buttons
  of its local player and receives everyone else's. Because every game receives the same input, they all compute the
  same result, so no game objects need to be synchronized over the network.
- **No input lag, no slowdown.** Each game measures its ping and sends its input just early enough that nobody ever
  waits, so the game keeps its full 30 fps. Your own SOUL is still drawn without delay, and bullets hit it where you
  see it: your game decides your hits and tells the others, so dodging feels like single player.

### Roadmap

1. **Virtual input layer**: route every keyboard and gamepad read (game and mod) through one layer that returns
   network input for remote players.
2. **Transport**: a small native DLL (UDP, star topology through the host, automatic input delay from the ping).
3. **Determinism**: shared random seed; game logic must not depend on audio playback state or the system clock.
   The virtual audio clock is done; still open are system clock reads in gameplay code (20 entries in Chapter 5)
   and the mouse (a Chapter 5 battle attack reacts to the cursor). See [docs/DESIGN.md](docs/DESIGN.md).
4. **Desync detection**: every N frames compare a hash of the game state (positions, RNG state, flags).
5. **Patcher**: UndertaleModTool CLI scripts that apply the changes to the user's own files.

6. **Own SOUL**: your heart reacts without delay and is hit where you see it (done for the red SOUL); next: your
   character in the overworld and the FIGHT timing bar.
7. **Online-only quality of life** (planned):
   - **your own SOUL stands out in battles**: each player's heart is drawn clearly on their own screen and the
     other players' hearts are dimmed, so several hearts are no longer confused;
   - **shared camera that splits when players drift apart**: one camera while everyone is close, split
     screen once a player walks too far away.
8. **Session start**: send the host's save, mod settings (`keyconfig_mod.ini`) and language to every player
   automatically; for now they are copied by hand.
9. **Chapters 1 to 4**: port the patch; the hazard counts per chapter are in [docs/DESIGN.md](docs/DESIGN.md).

The first prototype targets Chapter 5 and is tested with two copies of the game on one PC before going over a network.

## Usage

Step-by-step guides for players (building, playing with a friend, settings, troubleshooting) are in the
[wiki](https://github.com/Czekoloczek/DeltaruneOnline/wiki).

You need Python 3.11 or newer, the [UndertaleModTool](https://github.com/UnderminersTeam/UndertaleModTool) CLI
and `drnet.dll` (build it with `native/build.sh`). Every player must build UndertaleModTool from the same commit:
a different version writes a different `data.win` from the same files.

The easiest way is `patcher/build.py`. It checks that your Chapter 5 and the mod are exactly the versions it knows,
applies the mod with xdelta3, runs `dronline_patch.py`, and writes `data.win` and `drnet.dll` to
`build/chapter5_windows/`. At the end it prints the SHA-256 of the new `data.win`:

```
python3 patcher/build.py --game "DELTARUNE" --mod "deltarunelocalmultiplayer_v19" \
    --utmt PATH/UndertaleModCli.dll --out build
```

Or run the patcher yourself on a Chapter 5 `data.win` that already has the mod. It writes `chapter5_online.win`
and puts `drnet.dll` next to it:

```
python3 patcher/dronline_patch.py --utmt PATH/UndertaleModCli.dll \
    --data "DELTARUNE/chapter5_windows/data.win" --out chapter5_online.win
```

To install, whichever way you built it:

1. Copy your whole DELTARUNE folder and play from the copy. Leave the Steam folder as it is: Steam can put its own
   files back when it updates or verifies the game, and then your `data.win` no longer matches your partner's.
2. Put both files into the copy's `chapter5_windows` folder: the patched file as `data.win` (rename
   `chapter5_online.win` if you ran the patcher yourself) and `drnet.dll` next to it.
3. Compare the SHA-256 of that `data.win` with every other player's. `build.py` prints it; otherwise run
   `certutil -hashfile data.win SHA256` on Windows or `sha256sum data.win`. If they differ, the games will not stay
   in sync: check that everyone started from the same game, mod and UndertaleModTool versions.

Without a config file the patched game behaves like the original. To enable a mode, copy
`config/dronline.ini.example` to the save folder as `dronline.ini`. See [docs/DESIGN.md](docs/DESIGN.md) for how it
works and how to test determinism.

## Requirements

Every player needs:

- their own copy of DELTARUNE (current Steam version),
- the Local Multiplayer mod by Yomsman, downloaded from [GameBanana](https://gamebanana.com/mods/601376).

## Known challenges

From a review of the Chapter 5 code (game patch 0.253 + Local Multiplayer v19):

- random number calls in about 660 code files,
- about 36 files that depend on audio state (for example the rhythm minigame),
- about 52 files that read the system clock,
- input is read directly in many places of both the game and the mod,
- every chapter is a separate `data.win`, so changes have to be redone after game and mod updates.

## Contributing

- **Never commit game or mod files**: `data.win`, `.xdelta` patches, decompiled code, sprites, audio, or built output.
  The `.gitignore` blocks the common ones.
- Express changes as code and rules that the patcher applies to the user's local files, not as copies of modified
  game or mod code.
- Say which chapter, game version and mod version a change targets.
- Before opening a pull request, run `bash tools/verify.sh`: unit tests, the `drnet.dll` build and the lockstep test,
  all without game files. `bash tools/setup_env.sh` installs what it needs (Python 3.11+, mingw-w64, Wine).
- Test with two copies of the game on one PC before testing over a network.

## Credits

- **Local Multiplayer mod** by Yomsman, based on the multiplayer mods by Across_the_games and -S-.
- **DELTARUNE** by Toby Fox.

This project is not affiliated with Toby Fox or the authors of the Local Multiplayer mod.

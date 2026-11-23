# DeltaruneOnline

Online multiplayer for DELTARUNE, built on top of the [Local Multiplayer mod](https://gamebanana.com/mods/601376) by Yomsman.
Every player runs the game on their own PC, and the games stay in sync by exchanging only controller input each frame.

> **Status:** early planning. Nothing is playable yet.

> **Vibecoded:** this project is made with AI (Claude): code, analysis and documentation.

## How it works

- **You build it locally.** The patcher takes files you already have: your copy of DELTARUNE and the Local Multiplayer mod
  downloaded from GameBanana. It applies the changes on your machine. No game or mod files are distributed here.
- **Input lockstep.** All players run the full game from the same save. Each frame, every game sends the pressed buttons
  of its local player and waits for the others (with a small input delay to hide latency). Because every game receives
  the same input, they all compute the same result, so no game objects need to be synchronized over the network.

### Roadmap

The full plan, with findings, phases, tasks and risks, is in [ROADMAP.md](ROADMAP.md). In short:

0. **Tools and build pipeline**: rebuild the modded game from the player's own files with one command.
1. **Spikes**: small experiments that answer the risky questions (networking, waiting for the partner, replays).
2. **Virtual input layer**: route every keyboard and gamepad read (game and mod) through one layer that can take
   input from the network.
3. **Session setup**: the host sends the save file, settings, language and random seed.
4. **Lockstep loop**: GameMaker networking (`network_*`), input delay, waiting for the partner. First playable
   prototype.
5. **Determinism**: per-frame shared random seed, no gameplay dependence on audio playback or the system clock,
   desync detection.
6. **All chapters** and chapter changes.
7. **Patcher and release**.

The first prototype targets Chapter 5 and is tested with two copies of the game on one PC before going over a network.

## Requirements

Every player needs:

- their own copy of DELTARUNE (current Steam version),
- the Local Multiplayer mod by Yomsman, downloaded from [GameBanana](https://gamebanana.com/mods/601376).

## Known challenges

From a review of the Chapter 5 code (game patch 0.253 + Local Multiplayer v19):

- random number calls in about 660 code files,
- about 37 gameplay files that depend on audio state (for example the rhythm minigame),
- about 27 gameplay files that read the system clock (more in drawing code, where it is harmless),
- input is read directly in many places of both the game and the mod,
- every chapter is a separate `data.win`, so changes have to be redone after game and mod updates.

## Contributing

- **Never commit game or mod files**: `data.win`, `.xdelta` patches, decompiled code, sprites, audio, or built output.
  The `.gitignore` blocks the common ones.
- Express changes as code and rules that the patcher applies to the user's local files, not as copies of modified
  game or mod code.
- Say which chapter, game version and mod version a change targets.
- Test with two copies of the game on one PC before testing over a network.

## Credits

- **Local Multiplayer mod** by Yomsman, based on the multiplayer mods by Across_the_games and -S-.
- **DELTARUNE** by Toby Fox.

This project is not affiliated with Toby Fox or the authors of the Local Multiplayer mod.

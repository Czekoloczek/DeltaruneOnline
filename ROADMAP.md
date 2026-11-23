# Roadmap: from Local Multiplayer to Online

This document plans how to turn Yomsman's **Local Multiplayer mod (v19)** into an **online** mod, where two players
each run DELTARUNE on their own PC and play together over the internet.

It is written for people who are not GameMaker experts, so each choice says *why*, not only *what*.
Specialist words are explained in the [glossary](#glossary) at the end.

**Targets:** DELTARUNE 1.05 (Steam, Chapters 1 to 5) + Local Multiplayer v19. **First prototype: Chapter 5.**

---

## 1. Short version

- The local mod already sends **all of Player 2's buttons into one place** in the game's memory. That is the lucky
  part: to go online, we mostly change *where Player 2's buttons come from* (the network instead of the local
  keyboard), instead of rewriting the game.
- Both PCs run the **whole game** from the same save. Every frame, each PC sends only its own player's buttons
  (a few bytes). If both games get exactly the same buttons on exactly the same frame, they compute exactly the same
  game. This is called **input lockstep**.
- The hard part is not networking. It is **determinism**: finding every place where the two games could behave
  differently even with the same buttons (random numbers, the PC clock, audio timing, files on disk, mouse, language).
- The work is split into 8 phases. Phases 0 to 4 give a first playable online prototype in Chapter 5. Phases 5 to 7
  make it reliable, extend it to all chapters, and package it.

---

## 2. What we found in the Local Multiplayer mod

All of this was checked on 2026-11-23 by applying the mod to the game files and decompiling every chapter with
UndertaleModTool (see [Phase 0](#phase-0-tools-and-build-pipeline)). No game or mod code is copied into this repository;
only names of objects and variables are mentioned.

### How the mod is delivered

- Five `.xdelta` patches, one per chapter `data.win`. The game files we have match the checksums the mod expects
  for all five chapters, so the patches apply cleanly.
- The chapter select launcher (`DELTARUNE.exe` + the top-level `data.win`) is **not** changed by the mod.
- The game is built with GameMaker (bytecode 17, the normal "VM" build, not the compiled "YYC" build). That matters:
  VM games can be edited with UndertaleModTool, which is what makes this whole project possible.

### How big the mod's changes are

| Chapter | Code entries in game | Entries changed by mod* | Lines changed* |
|--------:|---------------------:|------------------------:|---------------:|
| 1 | 1,293  | 79    | 6,355  |
| 2 | 5,840  | 216   | 10,917 |
| 3 | 7,603  | 437   | 16,658 |
| 4 | 10,268 | 560   | 17,909 |
| 5 | 11,853 | 1,520 | 27,807 |

\* Upper bound: some differences are only cosmetic, caused by the mod being recompiled with a different tool version.

### How player input works (the key finding)

The same design is used in all five chapters:

1. **One object polls all input.** Every frame, at the very start (the "Begin Step" of the object `obj_time`), the game
   reads the keyboard and gamepads and writes the result into three global tables:
   `global.input_held`, `global.input_pressed`, `global.input_released`.
2. **Each player has their own slots in those tables.**

   | Player | Slots | Buttons |
   |---|---|---|
   | P1 (Kris) | 0 to 9 | down, right, up, left, confirm (Z), cancel (X), menu (C), Enter, Shift, Ctrl |
   | P2 (Susie) | 200 to 205 | down, right, up, left, confirm (J), cancel (I) |
   | P3 (Ralsei/Noelle) | 300 to 305 | same as P2 |

3. **The rest of the game asks small helper functions** like `up_h(player)` or `button1_p(player)` instead of reading
   the keyboard itself. The mod added a `player` argument: 0 = P1, 1 = P2, 2 = P3, and in Chapters 4 and 5 also
   3 = "any player".
4. **P2 is a party member, not a new object.** On the overworld, P2 controls Susie through the follower object
   `obj_caterpillarchara` (the mod grows its step code from about 250 to about 1,600 lines). In battle, the extra SOULs
   for P2 and P3 live inside the same `obj_heart` object as P1's SOUL.

**Why this is good news:** if we fill slots 200 to 205 from the network instead of from the local keyboard, most of the
game and the mod will "just work" without changes.

### Where input bypasses that system (must be fixed)

- **Mod hotkeys** read the keyboard directly: teleport to P1 (`P`, gamepad Start), player count (`F1`/`F2`/`F3`),
  noclip (`N`). They are stored as `global.input_k[800..803]`.
- **P1's gamepad LB** is read directly inside `button1_p`.
- **Raw keyboard reads in the game itself**: in Chapter 5, 75 code files call `keyboard_check...` or
  `sunkus_kb_check...` directly. Many are debug or test objects, but some are real gameplay (minigames, the
  platformer player, the rhythm game).
- **Mouse reads**: 8 files in Chapter 5, mostly editor/test objects, plus one battle attack.

### Other facts that shape the plan

- **Mod settings** are stored in `keyconfig_mod.ini`: player amount, shared inventory, easier bosses, old rhythm game,
  "prefer Ralsei", feather invert, colours, controller numbers.
- **Chapter changes** use GameMaker's `game_change()`, which restarts the game with another `data.win` and passes
  launch parameters along. The game already reads launch parameters, so we can pass connection info through them.
- **Files** are almost always read and written through the game's own `ossafe_...` wrapper functions
  (372 calls in Chapter 1, only a handful bypass it). That gives us one place to redirect save files.
- **The game already has a "freeze everything" trick**: its console pause (in `obj_time`) pauses all audio and
  deactivates every object except itself. We may reuse it to make a game wait for its partner.
- **The game engine has networking built in** (`network_create_socket`, `network_send_udp`, TCP functions, an async
  networking event) and hashing (`buffer_crc32`, `buffer_md5`). No extra library is needed to start.
- **Verified:** UndertaleModTool's command-line tool can compile new networking code into the modded Chapter 1 and
  Chapter 5 files (7 seconds for Chapter 5), and the result decompiles back cleanly with nothing else changed.
  It has **not yet been run** in the real game on Windows; that is the first task of Phase 1.

---

## 3. How online play will work

### Options we considered

| Option | How it works | Verdict |
|---|---|---|
| **A. Remote play / streaming** (Parsec, Steam Remote Play Together) | One PC runs the game; the friend watches a video stream and sends buttons. | Works **today** with the local mod and zero code. Yomsman's README already suggests Parsec. Keep it as the fallback. |
| **B. State sync** | The host runs the game and constantly sends positions, HP, enemies, etc. to the other PC. | Would mean understanding and syncing thousands of objects in five chapters. Not realistic for a mod. |
| **C. Input lockstep** | Both PCs run the full game; only buttons are sent. | **Chosen.** Small, fits the mod's input design, and both players get full-quality local graphics. |

### Why lockstep fits DELTARUNE

- The game runs at a fixed **30 frames per second**, so there are only 30 input messages per second.
- One frame of input is tiny: P1 needs 10 bits, P2 needs 6 bits. A packet with 8 frames of history is under 40 bytes.
- The mod already gathers all player input in one place.

An everyday comparison: two people play chess by post on two identical boards. They only send their moves. As long as
both boards start the same and both people apply the same moves, the boards stay identical. If one person once moves
a piece by accident, the boards "desync" and never match again. Our job is to make sure that never happens, and to
notice quickly if it does.

### The basic loop

```
PC A (plays P1 / Kris)                        PC B (plays P2 / Susie)
reads own keyboard/gamepad                    reads own keyboard/gamepad
  -> schedules it for frame N+3                 -> schedules it for frame N+3
  -> sends it to B  ------------------------>   receives it
  receives it  <-----------------------------   sends it to A
writes P1 input into slots 0-9,               writes P1 input into slots 0-9,
       P2 input into slots 200-205                   P2 input into slots 200-205
runs frame N only when it has BOTH            runs frame N only when it has BOTH
```

- **Input delay.** A button pressed on frame N takes effect on frame N+3 (100 ms) on both PCs. This gives the packet
  time to travel, so most frames never wait. The delay should be adjustable (2 to 5 frames) because it depends on the
  distance between players.
- **Waiting (stalling).** If the partner's input for the next frame has not arrived, the game must not run that frame.
  It freezes and shows "Waiting for partner...". This is the price of lockstep: lag shows up as short freezes, never
  as the two games disagreeing.
- **Each PC plays with its own controls.** PC B reads its keyboard with normal P1-style keys (arrows, Z, X, C) and
  writes them into the P2 slots. Nobody needs to use WASD online.

### Who connects to whom

- Two players, **peer to peer**. One player is the **host** only for setting up the session: the host chooses the save
  file and settings and sends them, then both PCs are equal.
- Up to three players later (the local mod supports three).
- **Transport:** start with **TCP** (reliable and simple; GameMaker frames the messages for us). Every input packet
  already carries the last 8 frames, so moving the per-frame inputs to **UDP** later is easy if TCP causes hiccups
  on real internet connections. The save file and settings stay on TCP.
- **Getting through home routers:** first test on one PC, then on a home network (LAN), then between two homes over a
  VPN. **The first testers will use WireGuard**, with the server on one tester's PC:
  - Only the WireGuard server's PC needs one UDP port forwarded on its router (51820 by default). The game itself
    needs no port forwarding, because inside the tunnel both PCs see each other as if they were on one home network.
  - The game connects to the partner's **WireGuard address** (for example `10.8.0.2`), not their public internet
    address.
  - Windows Firewall often treats the WireGuard network as "Public" and blocks incoming connections. The PC that
    hosts the game session needs a firewall rule that allows the game's port (or `DELTARUNE.exe`) on that network.
  - Our packets are tiny (under 100 bytes), so WireGuard's smaller packet size limit (MTU) is no problem.
  - Either PC can host the game session. It does not have to be the PC that runs the WireGuard server.

  Other players can use any virtual LAN instead (Tailscale, ZeroTier, Radmin VPN) or plain port forwarding.
  A relay server is a possible later extra.

---

## 4. Phases

Each phase lists its tasks and a "done when" check. Sizes are rough: **S** = an evening, **M** = a few evenings,
**L** = weeks.

### Phase 0: Tools and build pipeline

Goal: anyone can rebuild the modded game from their own files with one command, and the repo never holds game files.

- [x] Check the game and mod versions: all five `data.win` files match the mod's checksums.
- [x] Apply the mod patches and decompile all five chapters (game alone and game + mod) to compare them.
- [x] Prove that UndertaleModTool's command-line tool can add networking code to Chapter 5.
- [ ] **S** Install the tools on the Windows PC: UndertaleModTool (GUI, for reading code), the .NET 10 SDK
  (for the command-line tool), Python 3, xdelta3, git.
- [ ] **M** Write the build script (`patcher/build.py`):
  1. check the SHA-256 of the player's own `data.win`,
  2. apply the player's own copy of Yomsman's `.xdelta`,
  3. run our UndertaleModTool scripts (`patcher/ch5/*.csx`) that add new code and make small, anchored edits,
  4. write the result into a **separate copy** of the game folder.
- [ ] **S** Rule for every edit: find a short, unique anchor in the existing code and change only around it. If the
  anchor is missing (the game or mod was updated), the build stops with a clear message instead of producing a broken
  game.
- [ ] **S** A local, git-ignored `decompiled/` folder for reading code, produced by the same script.
- [ ] **S** Two copies of the game on one PC for testing (see [risks](#5-risks-and-open-questions): both copies share
  the same save folder, which Phase 3 fixes).

Done when: `build.py` turns a clean Chapter 5 into "Chapter 5 + local mod + our patch" in one run, and the game
starts.

### Phase 1: Spikes (small experiments that answer the risky questions first)

Goal: learn the few things that decide the design before writing much code. Each spike is a throw-away build.

- [ ] **S** *Network hello:* the test build already compiles. Run two copies on one PC and check that a message sent
  by one arrives in the other's async networking event.
- [ ] **S** *Event timing:* log the frame on which async networking events arrive compared with `obj_time`'s Begin Step.
  This decides where the "may I run the next frame?" check lives.
- [ ] **M** *Freeze test:* freeze the game for a few frames with the console-pause trick (deactivate all objects, pause
  audio), then resume. Hash the list of all objects and their order before and after. If the order or any value
  changes, this trick cannot be used for waiting, and we use plan B: a tiny Windows DLL extension that can wait for a
  network message (Chapter 5 already ships with two DLL extensions, so the game supports them).
- [ ] **M** *Replay test:* record P1's and P2's inputs to a file during a short play session, then replay the file twice
  in fresh games and compare a state hash every second. This tells us how deterministic Chapter 5 is **before** any
  networking.
- [ ] **S** *Chapter change:* check that a network socket does not survive `game_change()` (expected), so we plan to
  reconnect.

Done when: each question has a written answer in `docs/spikes.md`.

### Phase 2: Virtual input layer (the heart of the mod)

Goal: every input that can change the game goes through one layer that can take input from the keyboard, from a
replay file, or from the network.

- [ ] **M** Add online state: `global.dro_mode` (off / host / client), `global.dro_local_player` (0 or 1) and a frame
  counter that counts game frames.
- [ ] **M** Change the polling in `obj_time`'s Begin Step:
  - read the local devices with the P1 key and gamepad settings,
  - pack them into a small bit mask and schedule it for frame N + delay,
  - take the masks for the current frame from the input queue (local + remote),
  - unpack P1's mask into slots 0 to 9 and P2's mask into slots 200 to 205, and work out "pressed" and "released"
    from the previous frame, exactly as the game does now.
- [ ] **S** Put the special inputs into the mask: teleport (`P` / Start) and noclip (`N`). Lock the player count to 2
  online, so `F1`/`F2`/`F3` do nothing.
- [ ] **S** Remove the direct gamepad LB read from `button1_p`; LB becomes a bit in P1's mask.
- [ ] **L** Raw keyboard reads in game code (75 files in Chapter 5): replace `keyboard_check...` / `sunkus_kb_check...`
  with one wrapper. Online, the wrapper answers from the synced mask: keys that belong to a button (Z, X, C, Enter,
  arrows, Shift, Ctrl) map to that button of P1, and other keys return "not pressed". Debug-only reads stay off.
  Each changed file is listed in `docs/input-audit.md`.
- [ ] **S** Keep **local-only** keys local and make sure they never change the game: quit (`Esc`), fullscreen (`F4`),
  window and volume.
- [ ] **M** Input recording and replay to a file, using the same layer (needed for Phase 1 and Phase 5 testing).

Done when: the game plays normally offline through the new layer (no change for the player), and a recorded session
replays to the same result.

### Phase 3: Session setup and a shared starting point

Goal: both games start from exactly the same state.

- [ ] **S** Start online mode through launch parameters, for example `DELTARUNE.exe -dro_host 7777` and
  `-dro_join 100.64.0.5:7777`. An in-game menu inside the mod's config screen can come later.
- [ ] **S** Handshake: both sides send the game version, mod version and a hash of the patched `data.win`. Any
  difference stops the session with a clear message.
- [ ] **M** The host sends everything that affects the game:
  - the chosen **save file** (the client uses it but never overwrites its own saves),
  - the **mod settings** that affect gameplay: shared inventory, easier bosses, old rhythm game, prefer Ralsei,
    feather invert (player amount is forced to 2),
  - the **language**, because English and Japanese text have different lengths and dialogue would finish on
    different frames,
  - a shared **random seed**,
  - any other file the game reads during play (for example the files that record finished chapters).
- [ ] **S** Separate files for two copies on one PC: the client adds a prefix to file names inside the `ossafe_...`
  wrappers, so it never touches the host's saves.
- [ ] **S** Both sides start on the same frame 0, set the seed and load the save.

Done when: two games on one PC start online and report the same state hash on frame 0.

### Phase 4: Lockstep loop and transport (first playable prototype)

Goal: two people can walk, talk and fight together in Chapter 5.

- [ ] **M** Message types: `HELLO`, `CONFIG`, `SAVE`, `START`, `INPUT` (first frame + last 8 frames of masks),
  `HASH`, `PING`, `BYE`.
- [ ] **M** Input queue with adjustable delay (start at 3 frames = 100 ms).
- [ ] **M** Waiting: freeze when the partner's input for the next frame is missing, using the method chosen in Phase 1.
  Pause audio while frozen and show "Waiting for partner...". After about 10 seconds, show a disconnect screen.
- [ ] **S** Show ping and the current input delay on screen (toggle).
- [ ] **S** Disable chapter changes in the prototype (Chapter 5 only).

Done when: two copies on one PC play 10 minutes of Chapter 5 overworld and a normal battle without a desync; then the
same on a home network; then over WireGuard between two homes.

### Phase 5: Determinism hardening (the biggest phase)

Goal: find and remove everything that can make the two games differ. This phase never fully ends; each fix is tested
with replays.

- [ ] **M** Desync detector: every 30 frames (1 second) both sides hash a summary of the game (room, frame number,
  number of objects of each type, positions of Kris, Susie and the SOULs, HP, money, story flags) and send the hash.
  On a mismatch, both sides save a full state dump to a file and the session pauses, so the bug can be found.
- [ ] **M** **Random numbers** (661 files in Chapter 5 use them): at the start of every frame both sides re-seed the
  random generator from `(shared seed, frame number)`. Then a difference in one frame cannot spread to later frames.
  The game's own re-seeding calls (`randomize()` and `random_set_seed()`, 8 calls in Chapter 5) are changed to use
  the shared seed.
- [ ] **M** **Random numbers inside drawing code** (85 Draw-event files in Chapter 5): drawing can behave differently
  per PC (for example when a window is minimised). The per-frame re-seed already limits the damage; if needed, the
  patcher sends Draw-event random calls to a separate "visual only" generator.
- [ ] **L** **PC clock in gameplay code** (27 non-drawing files in Chapter 5, for example the rhythm game, music events,
  the dash, Flowery's runner and shooter, platformer objects, the save play-time): replace with time computed from the
  frame counter (frames × 1000 / 30 milliseconds).
- [ ] **L** **Audio state in gameplay code** (37 non-drawing files in Chapter 5, for example "wait until this sound
  ends" in cutscenes, the music tracker, lip-sync, the rhythm game): real audio runs on its own clock and can finish a
  frame earlier or later on each PC. Replace these checks with a "virtual audio clock" based on the frame counter and
  the sound's length. Real sound still plays as normal.
- [ ] **L** **Rhythm game** (`obj_rhythmgame`): the hardest part, because it is driven by the music position. Make it
  use the virtual audio clock; until then, mark it as "not supported online" and offer a skip.
- [ ] **S** **Mouse** (8 files): most are editors and tests, but in one battle attack (`obj_attack_blue_flowerbullet`)
  a flower bullet explodes when the mouse cursor is over it. Online, the mouse position must be ignored, or the two
  PCs would see different explosions.
- [ ] **M** **Files read during play** (36 files in Chapter 5): make sure both PCs see the same content, using what the
  host sent in Phase 3.
- [ ] **L** **Playthrough tests**: play Chapter 5 section by section online, keep the replays, and re-run them after every
  change. Keep a public list of sections that are known to work.

Done when: a full Chapter 5 playthrough online finishes without a desync, or with a short, documented list of
unsupported sections.

### Phase 6: All chapters and chapter changes

Goal: play the whole game online.

- [ ] **M** Chapter changes: pass the session info through `game_change()`'s launch parameters, reconnect automatically
  when the new chapter starts, and re-send save, settings and seed.
- [ ] **M per chapter** Port Phases 2 to 5 to Chapters 4, 3, 2, 1. The input design is the same in all chapters, so the
  online layer itself carries over. The determinism work differs per chapter:

  | Chapter | Random-number files | of them in Draw | Clock (gameplay) | Audio state (gameplay) | Raw keyboard files |
  |---:|---:|---:|---:|---:|---:|
  | 1 | 113 | 14 | 1  | 1  | 7   |
  | 2 | 524 | 83 | 3  | 38 | 46  |
  | 3 | 662 | 74 | 11 | 28 | 129 |
  | 4 | 521 | 81 | 32 | 41 | 208 |
  | 5 | 661 | 85 | 27 | 37 | 75  |

  (Counts are files that use the feature at least once, in game + mod code, found with simple text search.)
- [ ] **S** Chapter select launcher: either patch it lightly or start each chapter directly.

Done when: two players can go from one chapter to the next without restarting the session by hand.

### Phase 7: Packaging and release

- [ ] **M** A Windows-friendly patcher (double-click script or small window) that does the Phase 0 build on the
  player's own files.
- [ ] **S** Install guide with screenshots, including how to connect over WireGuard (and alternatives such as
  Tailscale, ZeroTier or Radmin VPN), with the Windows Firewall rule.
- [ ] **S** Ask Yomsman for permission before publishing, and credit the original authors.
- [ ] **S** Version checks: if DELTARUNE or the local mod updates, the patcher refuses to run until we update the
  anchors.

### Later ideas (not needed for version 1)

- Three online players.
- Re-joining after a disconnect (needs a saved state both sides agree on).
- A relay server so nobody needs a VPN.
- Each player's camera following their own character. Careful: the game uses the camera position in game logic (for
  example where bullets appear), so only the drawing may move, not the logic camera.

---

## 5. Risks and open questions

Ordered from most to least likely to cause trouble.

| # | Risk | What we do about it |
|---|---|---|
| 1 | **Hidden differences between the two games** (timing, audio, files) cause desyncs that are hard to find. | Build the replay tool and the desync detector early (Phases 1, 2 and 5). Accept a list of unsupported sections at first. |
| 2 | **Waiting for the partner without changing the game.** GameMaker cannot simply "pause and wait" for a network message. | Phase 1 freeze test; plan B is a small DLL that can wait. |
| 3 | **Two copies on one PC share the save folder** (`%LOCALAPPDATA%\DELTARUNE`), and only the focused window gets keyboard input. | The client uses prefixed file names (Phase 3). For one-PC tests, use a gamepad for the second copy or the replay tool. |
| 4 | **Game or mod updates** change the code we edit. | Small anchored edits that fail loudly; checksums in the patcher. |
| 5 | **Input delay feels sluggish** in timing-heavy parts (rhythm game, platforming). | Adjustable delay; play-test with real distances. |
| 6 | **Home routers or Windows Firewall block connections.** | WireGuard for the first testers (one forwarded UDP port on the server's router), a firewall rule for the game; document other VPNs and port forwarding. |
| 7 | **Chapter changes drop the connection.** | Reconnect through launch parameters (Phase 6). |
| 8 | **Community and legal:** game and mod files must not be shared. | The repo holds only our own code and scripts; players patch their own files. Ask Yomsman before release. |

Open questions to answer along the way:

- When exactly does GameMaker run the async networking event within a frame? (Phase 1)
- Does deactivating and reactivating all objects keep their order? (Phase 1)
- How deterministic is Chapter 5 with replays alone, before any fixes? (Phase 1)

---

## Glossary

- **data.win**: the file that holds a GameMaker game's code, graphics and sounds. Each DELTARUNE chapter has its own.
- **xdelta patch**: a file that describes the differences between two versions of a file. Applying Yomsman's patch to
  the original `data.win` produces the modded one.
- **UndertaleModTool (UTMT)**: the community tool that opens, decompiles and edits GameMaker `data.win` files.
- **GML**: GameMaker Language, the scripting language the game is written in.
- **Object / event**: GameMaker code is organised into objects (Kris, a bullet, a menu) and events (Create, Begin Step,
  Step, Draw...) that run at fixed moments of every frame.
- **Begin Step**: the first event of every frame, where the game reads input.
- **Async networking event**: the GameMaker event that runs when a network message arrives.
- **Lockstep**: both PCs advance one frame at a time, together, using the same inputs.
- **Determinism**: the same inputs always give the same result. Lockstep only works if the game is deterministic.
- **Desync**: the moment the two games stop being identical.
- **Input delay**: inputs take effect a few frames later than they were pressed, to give the network time.
- **Hash**: a short fingerprint of some data. If two hashes differ, the data differs.
- **Seed**: the starting value of the random number generator. Same seed, same "random" numbers.
- **TCP / UDP**: the two common ways to send data over the internet. TCP is reliable and ordered but can pause on lost
  packets; UDP is faster but can lose packets.
- **NAT / port forwarding**: home routers hide PCs behind one address. Port forwarding or a virtual LAN lets a friend
  reach your PC.
- **Spike**: a small throw-away experiment that answers one question.

# Phase 1: spikes (small experiments)

This page belongs to [Phase 1 of the roadmap](../ROADMAP.md#phase-1-spikes-small-experiments-that-answer-the-risky-questions-first).
Each spike answers one risky question before we build the real online mode. The answers go in the
**Results** sections below, filled in after testing on Windows.

**Test build:** `DRO phase1 test 1` = DELTARUNE 1.05 Chapter 5 + Local Multiplayer v19 + `patcher/ch5/net_test.csx`.

---

## What the test build adds

One new invisible object, `obj_dro_net`, that lives through every room, plus a small panel in the top left
corner of the screen. It does **not** change gameplay: it only reads the game's input slots and the list of
objects. The one exception is the freeze test (F8), which pauses the whole game for 2 seconds on purpose.

| Key | What it does |
|---|---|
| **F5** | Host: wait for a partner on TCP port 7777. |
| **F6** | Join: type the host's IP address (the last one is remembered), then press **Enter**. |
| **F7** | Stop: close the connection. |
| **F8** | Freeze test: pause this game for 2 seconds, then resume and check that nothing changed. |
| **F9** | Panel size: short, full statistics, hidden. |

Once two games are linked, each game sends **one small message every frame** (30 per second) with its frame
number and the buttons Player 1 is holding. The panel shows what arrives from the partner, including the
partner's buttons, so you can see the input travel live.

Files it writes, all in the game's save folder (`%LOCALAPPDATA%\DELTARUNE`):

- `deltarune_online.ini`: the port (default `7777`) and the last IP you joined.
- `online_test_host.txt`, `online_test_join.txt`, `online_test_offline.txt`: logs with a summary every 30 seconds
  and when the connection ends.

How it is added: `patcher/ch5/net_test.csx` (an UndertaleModTool script) checks that the file really is Chapter 5
with the local mod, adds the new object from `patcher/ch5/net_test/*.gml`, and appends four lines to the end of
`obj_time`'s Create event that create the object. It was checked by decompiling the result: only those files
changed. `patcher/build.py` runs the whole build from your own game and mod files.

---

## How to test

### Step 1: make a test copy of the game (once)

1. In Steam, right-click **DELTARUNE**, then **Manage**, then **Browse local files**.
2. Go up one folder, copy the `DELTARUNE` folder and paste it somewhere else, for example on the Desktop.
   Rename the copy to `DELTARUNE Online Test`. Never change the Steam folder itself.
3. In the copy, open `chapter5_windows` and replace `data.win` with the test build's `data.win`.
   The test build already contains the Local Multiplayer mod for Chapter 5.

### Step 2: two windows on one PC

1. In `DELTARUNE Online Test`, double-click `DELTARUNE.exe` and choose Chapter 5.
   The panel `ONLINE TEST: OFF` appears in the top left corner.
2. Double-click `DELTARUNE.exe` **again** for a second window, and choose Chapter 5 there too.
   Put the two windows side by side. Only the window you clicked last gets the keyboard.
3. Window 1: press **F5**. If Windows asks about the firewall, tick **both** Private and Public networks and
   click **Allow**. (One PC does not need it, but the WireGuard test does.)
4. Window 2: press **F6**. `127.0.0.1` (this same PC) is already filled in. Press **Enter**.
5. Both panels turn green: `ONLINE TEST: LINKED`. Press **F9** if you do not see the statistics.
6. Click window 1 and hold some keys (arrows, Z, X). Window 2 shows them in the line `Partner holds: ...`.
7. Leave both windows running for 2 minutes. Do not save the game in these windows (both use the same saves).
8. Click window 1 and press **F8**. That game freezes for 2 seconds and continues. Its panel shows a
   `Freeze test ...` line. Window 2 shows `Partner silent` for a moment.
9. Take a screenshot of both panels (**Win + Shift + S**), then press **F7** in one window.

### Step 3: two PCs over WireGuard

1. Both PCs: turn on the WireGuard tunnel. In the WireGuard app, note each PC's tunnel address
   (under **Interface: Addresses**, for example `10.8.0.2`).
2. The partner makes the same test copy (with their own DELTARUNE and the same test `data.win`).
3. The PC that will host the game needs to let the game through Windows Firewall. If you clicked Allow for
   both network types in Step 2, this is done. Otherwise open **PowerShell as administrator** and run:

   ```powershell
   New-NetFirewallRule -DisplayName "DELTARUNE online test" -Direction Inbound -Protocol TCP -LocalPort 7777 -Action Allow
   ```

4. Host: start the game, choose Chapter 5, press **F5**.
5. Partner: start the game, choose Chapter 5, press **F6**, delete `127.0.0.1` with Backspace, type the host's
   tunnel address (for example `10.8.0.1`), press **Enter**.
6. When both are linked, play for about 5 minutes (walk around, open menus). Each side presses **F8** once.
7. Both press **F9** for the full panel and take a screenshot.

Either PC can host. It does not have to be the one that runs the WireGuard server.

### What to send back

- Did the game start normally with the panel? Any error window (screenshot it)?
- Screenshots of the panels from Step 2 and Step 3.
- The log files: press **Win + R**, type `%LOCALAPPDATA%\DELTARUNE`, press Enter, and send
  `online_test_host.txt` and `online_test_join.txt` from both PCs.

### If something goes wrong

| Message | Likely reason |
|---|---|
| `Could not open port 7777` | The other window on this PC is already hosting. Use F6 in this one. |
| `Could not reach ...` | The host did not press F5, the IP is wrong, the tunnel is off, or the firewall blocks it. |
| `Something else answered on that port` | Another program uses port 7777. Change `port` in `deltarune_online.ini` on both PCs. |
| `Partner has a different test version` | The two PCs have different test builds. |

---

## Spike 1: network hello

**Question:** can two copies of the patched game connect and exchange a message every frame?

**How the build answers it:** status `LINKED`, the frame counters going up on both panels, and
`missing 0, late 0` in the panel.

**Results:**

| Test | Linked? | Ping (min/avg/max) | Missing / late frames | Notes |
|---|---|---|---|---|
| One PC (127.0.0.1) | | | | |
| WireGuard | | | | |

## Spike 2: event timing

**Question:** when, inside a frame, does GameMaker deliver network messages? This decides where the future
"do we have the partner's input for the next frame?" check goes.

**How the build answers it:** the panel line `Arrived in BeginStep a, Step b, EndStep c, after draw d` counts
which of our object's events ran last before each message arrived. The log also has five `Timing sample` lines.

- Mostly **after draw**: messages are handled between frames, before the next Begin Step. Then the check can live
  at the start of `obj_time`'s Begin Step, which is the simplest design.
- Mostly **EndStep**: messages are handled after the game logic but before drawing. The check still works at
  the start of the next Begin Step.
- Spread over several events: the check needs to be repeated or moved; we will look at the numbers together.

The line `Per frame 0:a 1:b 2:c 3+:d, longest wait n fr` shows how evenly messages arrive. Mostly `1` is ideal.
Many `0` and `2` means messages come in bunches; the input delay in Phase 4 must cover that.

The line `Frame: me x, partner y, diff d (min to max)` shows whether both games run at the same speed. If `diff`
stays in a small range, they do. If it keeps growing, one game is slower, and lockstep will have to make it wait.

**Results:**

| Test | Arrived in (BeginStep / Step / EndStep / after draw) | Per frame 0 / 1 / 2 / 3+ | Longest wait | Diff range |
|---|---|---|---|---|
| One PC | | | | |
| WireGuard | | | | |

## Spike 3: freeze test

**Question:** can we pause the game while waiting for the partner, using the trick from the game's own console
pause (deactivate all objects, pause audio), without changing anything?

**How the build answers it:** F8 lists every object, deactivates all of them except `obj_dro_net` at the very end
of a frame, waits 60 frames, wakes exactly the same objects again and compares the list. The panel shows
`Freeze test OK` or `FAILED`, how many objects came back at once, and how many moved.

- `OK` and `moved 0`: the trick keeps the object order and positions. Phase 4 can use it to wait.
- `OK one frame later`: it works, but objects come back one frame late; Phase 4 must allow for that.
- `FAILED`: we need plan B (a small DLL that can wait for a network message).

Also worth noting: does the music continue correctly after the freeze, and does anything look wrong?

**Results:**

| Where in the game | Result line | Music OK? | Notes |
|---|---|---|---|
| | | | |

## Spike 4: replay test (moved to Phase 2)

Recording and replaying P1 and P2 input needs the virtual input layer from Phase 2 (something has to write the
recorded buttons into the input slots). It is cheaper to build it once there than to build a throw-away version
now. The roadmap is updated accordingly.

## Spike 5: chapter change (optional)

**Question:** does the connection survive a chapter change (`game_change()`)? Expected answer: no, so Phase 6
reconnects automatically.

**How to test, if you can reach the chapter select screen from inside Chapter 5:** while linked, go back to
chapter select in the joining window and note what the host's panel says (expected: `Partner disconnected`).

**Results:**

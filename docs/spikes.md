# Phase 1: spikes (small experiments)

This page belongs to [Phase 1 of the roadmap](../ROADMAP.md#phase-1-spikes-small-experiments-that-answer-the-risky-questions-first).
Each spike answers one risky question before we build the real online mode. The answers go in the
**Results** sections below, filled in after testing on Windows.

**Test build:** `DRO phase1 test 2` = DELTARUNE 1.05 Chapter 5 + Local Multiplayer v19 + `patcher/ch5/net_test.csx`.

| Build | Date | Changes | Tested |
|---|---|---|---|
| test 1 | 2026-11-23 | first version | one PC, 2026-11-23 (results below) |
| test 2 | 2026-11-23 | freeze test runs 5 variants and logs the object order; "Partner silent" after 0.5 s; protocol 2 | not yet |

---

## What the test build adds

One new invisible object, `obj_dro_net`, that lives through every room, plus a small panel in the top left
corner of the screen. It does **not** change gameplay: it only reads the game's input slots and the list of
objects. The one exception is the freeze test (F8), which pauses the whole game three times for 1 second on purpose.

| Key | What it does |
|---|---|
| **F5** | Host: wait for a partner on TCP port 7777. |
| **F6** | Join: type the host's IP address (the last one is remembered), then press **Enter**. |
| **F7** | Stop: close the connection. |
| **F8** | Freeze test: five short pauses in a row (about 4 seconds in total), each checking whether the game's object list is unchanged afterwards. |
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
   click **Allow**. One PC does not need it, but the WireGuard test does: Windows usually treats the WireGuard
   network as **Public**, so Private alone is not enough.
4. Window 2: press **F6**. `127.0.0.1` (this same PC) is already filled in. Press **Enter**.
5. Both panels turn green: `ONLINE TEST: LINKED`. Press **F9** if you do not see the statistics.
6. Click window 1 and hold some keys (arrows, Z, X). Window 2 shows them in the line `Partner holds: ...`.
7. Leave both windows running for 2 minutes. Do not save the game in these windows (both use the same saves).
8. Click window 1 and press **F8**. That game pauses three times for 1 second and continues. Its panel shows five
   `Freeze ...` result lines. Window 2 shows `Partner silent` during each pause.
   Do this once on the title or file select screen, and once after loading a save (more objects in the room).
   Listen whether the music continues normally afterwards.
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

Tip for repeated tests: starting `DELTARUNE.exe -game "<test copy>\chapter5_windows\data.win"` with
`chapter5_windows` as the working folder (for example in a shortcut's "Start in" field) skips the chapter select
and opens Chapter 5 directly.

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
| One PC (127.0.0.1), test 1 | yes, 3 min 1 s | 33 / 33-34 / 66 ms | 0 / 0 (5396 frames each way), send errors 0 | 30 FPS, slowest frame 48 ms |
| WireGuard | | | | |

The ping cannot be lower than one frame (33 ms): the reply to a ping is sent from the network event, which runs once
per frame, and is read in the next one.

## Spike 2: event timing

**Question:** when, inside a frame, does GameMaker deliver network messages? This decides where the future
"do we have the partner's input for the next frame?" check goes.

**How the build answers it:** the panel line `Arrived in BeginStep a, Step b, EndStep c, after draw d` counts
which of our object's events ran last before each message arrived. The log also has five `Timing sample` lines.

- Mostly **Step** (this is what test 1 found): GameMaker handles network messages after the Step event and before
  the End Step of every frame. Everything that arrived is known by the next Begin Step, so the check can live at the
  start of `obj_time`'s Begin Step.
- Mostly **after draw**: messages are handled between frames, before the next Begin Step. Same conclusion.
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
| One PC, test 1 | 0 / 5396 / 0 / 0 (both sides) | 0: 1-2, 1: 5394-5397, 2: 0-1 | 1 frame | host 1 to 1, join 0 to 0 |
| WireGuard | | | | |

**Answer (one PC):** network events arrive at one fixed point in every frame, between Step and End Step, for all
5396 messages. A message sent in the End Step of one frame arrives during the partner's next frame. So the input for frame N+D, sent at the end of frame N, is
available at the start of frame N+D as long as the network delay stays under D-1 frames. Both games ran at exactly
the same speed (the frame difference never changed outside the freeze test). The WireGuard test will show how much
delay real distance adds.

## Spike 3: freeze test

**Question:** can we pause the game while waiting for the partner, using the trick from the game's own console
pause (deactivate all objects, pause audio), without changing anything?

**Test 1 result:** on the intro screen (20 objects) and the file select screen (5 objects) the same pattern twice:
no object was lost and none moved, but woken objects are only back at the **next** frame (`instance_activate_object`
takes effect later, not in the same event), and the object list came back in a **different order**. Order matters
for lockstep: GameMaker runs objects' events in that order, and only the waiting PC would be reordered, so the two
games could start to differ.

**Test 2:** one F8 press runs five variants, three normal frames apart. Each one lists all objects at the end of a
frame (Draw GUI End) and again at the start of the next frame (Begin Step), when woken objects are back:

| # | Variant | What it does |
|---|---|---|
| 0 | control | Nothing is paused. Shows whether the list order is stable by itself from one frame to the next. |
| 1 | forward | Pause everything for 30 frames, wake each object in the old order. |
| 2 | reverse | Same, but wake them in reverse order. |
| 3 | activate_all | Same, but wake with `instance_activate_all()`, like the game's own console pause. |
| 4 | same frame | Pause and wake in the same event, without skipping a frame. |

Each result line says `same order`, `REVERSED` or `REORDERED`, whether each object type at least kept the order of
its own instances, how many objects came back, how many are new, and how many moved. When the order changed, the log
also lists the object names before and after, so we can see the pattern.

How to read it:

- Control `same order` and some wake variant `same order`: that variant can be used for waiting in Phase 4.
- Control `same order` but every wake variant reordered: if the change is always the same (for example always
  reversed, or always grouped by object type), both PCs could do the same pause-and-wake on every frame, so they
  stay in the same order (variant 4 tests whether that keeps the game running normally). Otherwise plan B: a
  small DLL that can wait for a network message, so the game never has to deactivate anything.
- Control already `REORDERED`: the list order is not a useful measure, and we look at the event order differently.

**Results:**

| Build | Where in the game | Result | Music OK? | Notes |
|---|---|---|---|---|
| test 1 | intro (20 objects) | FAILED: 0 back in the same event, all 20 back next frame in a different order, moved 0 | not checked | |
| test 1 | file select (5 objects) | same as above with 5 objects | not checked | |
| test 2 | | | | |

## Spike 4: replay test (moved to Phase 2)

Recording and replaying P1 and P2 input needs the virtual input layer from Phase 2 (something has to write the
recorded buttons into the input slots). It is cheaper to build it once there than to build a throw-away version
now. The roadmap is updated accordingly.

## Spike 5: chapter change (optional)

**Question:** does the connection survive a chapter change (`game_change()`)? Expected answer: no, so Phase 6
reconnects automatically.

**How to test:** Chapter 5's file select menu has a way back to the chapter select (it calls the game's
`scr_chapterswitch`). While linked, go back to chapter select in the joining window and note what the host's panel
says (expected: `Partner disconnected`). Then do the same with the **hosting** window, pick Chapter 5 again and press
F5: if it says `Could not open port 7777`, the old connection survived the chapter change inside the game.

**Results:**

# Design

## Where input comes from

DELTARUNE computes all player input once per frame in the **Begin Step of `obj_time`**:
it reads keys from `global.input_k[]` and gamepad buttons from `global.input_g[]` and fills
`global.input_pressed[]`, `global.input_held[]` and `global.input_released[]`.

| Player | Action slots | Default keys |
|---|---|---|
| P1 | 0 down, 1 right, 2 up, 3 left, 4 confirm, 5 cancel, 6 menu, 7–9 alternates (Enter, Shift, Ctrl) | arrows, Z, X, C |
| P2 (mod) | 200–203 directions, 204 confirm, 205 cancel | WASD, J, I |
| P3 (mod) | 300–303 directions, 304 confirm, 305 cancel | TFGH, M, K |
| mod extras | 800 teleport, 801–803 player amount | P, F1–F3 |

Besides that, about 330 direct reads (`keyboard_check*`, `gamepad_*`) are spread over ~66 code entries
of the game and the mod (Chapter 5, game 0.253 + mod v19).

## Virtual input layer (`gml/scr_dronline.gml`)

1. Every machine reads **its own** keyboard and gamepad and turns them into **action bits** of its local
   player, using its own key config (bit 0 down, 1 right, 2 up, 3 left, 4 confirm, 5 cancel, 6 menu,
   7 teleport, 8–10 F1–F3).
2. The bits of all players are combined into a **virtual keyboard**: player *p*'s action *a* presses
   `global.input_k[base_p + a]`. All virtual gamepads report "disconnected", so the game and the mod
   only ever see the virtual keyboard.
3. The patcher rewrites every built-in input read to the `dro_*` functions, which answer from the
   virtual keyboard (or pass through to the real device when the online mode is off).
4. `randomize()` becomes `dro_randomize()`, which seeds every machine with the same seed.

Because every machine feeds the same bits into the same game state, every machine computes the same frame.

## Lockstep

Each frame *F*, a machine sends its bits for frame *F + D* and waits until it has every player's bits
for frame *F*. Waiting has to block inside the frame, which GameMaker's asynchronous networking cannot do, so
the native DLL `native/drnet.c` (Windows build, also under Proton) handles UDP, resends and the blocking wait.
A network thread receives packets the moment they arrive, so the ping is measured exactly and the host relays
input without waiting for its own next frame. Star topology: clients talk only to the host, which relays
everyone's input, so only the host needs an open UDP port (default 7845). Every packet carries the sender's
last 16 frames, so a lost packet is covered by the next one. State fingerprints are exchanged every 30 frames
to detect desyncs.

Neither input delay nor a slowing game is acceptable in DELTARUNE, where a few milliseconds decide whether
a bullet hits, so the design avoids both:

- **Automatic delay, so the game never waits.** Each machine measures its ping (timestamps echoed in every
  packet) and picks its own *D*: the frames its input needs to reach every other player, minus the ~16 ms of a
  frame that a wait can use without delaying the frame. Below roughly 30 ms ping *D* is 0; at 80 ms ping it is
  1–2. *D* grows at once when the ping rises and shrinks after 3 s of clearly better ping. Players do not need
  to agree on it: every frame of every player is filled exactly once (a sample is repeated when *D* grows and
  merged into the next one when it shrinks), and the frames are what everybody agrees on.
- **Own SOUL without delay.** A delay would make your own heart react late, so your own heart is drawn where it
  will be once your already-sent input is applied, and bullets hit it there (next section). Everything else
  is shared state and stays in lockstep.

(Rollback, the textbook answer, would need saving and restoring the complete GameMaker state every frame:
instance ids cannot be recreated and data structure ids are plain numbers, so DELTARUNE gives no way to do it.)

`delay=<frames>` in `dronline.ini` fixes *D* instead; with a fixed 0 the game waits out the network every
frame and slows down above roughly 30 ms ping.

## Own SOUL

The shared simulation moves a heart *D* frames after its owner pressed the keys. On the owner's screen:

- **Drawing**: obj_heart's Draw temporarily moves the local player's heart to the shared position plus the
  moves already sent for later frames (`drnet_pending`), following obj_heart's red SOUL movement (speed, slow
  walk while cancel is held, walls of the box). Only the heart's own Draw sees that position, so the shared
  state never changes. Other SOUL modes keep the shared position.
- **Hits**: before sending a frame's input, the owner's machine checks whether a bullet touched the heart where
  it was drawn, and sends that bullet's instance number with the input (`drnet_set_aux`). Every machine applies
  it in the same frame through the game's own hit code: heart 1's collision event is skipped and its body runs
  from obj_heart's End Step with the reported bullet as `other`; the `instance_place` checks for hearts 2 and
  3 return the reported bullet. Instance numbers match on every machine because the simulation is identical.
- Frames sent without delay carry no `LAG_BIT` and keep the game's own collisions, since there the shared
  position already is what the owner sees. Purple/mode 7 and other special hit code stay shared too.

The price: the other players' hearts on your screen lag behind what they see by their delay, so now and then a
bullet seems to pass through another player's heart without a hit, or to hit it from a distance. Every hit
follows what the dodging player saw.

`native/test_drnet.c` checks the DLL alone: host and client exchange pseudo-random input and events under
Wine, optionally with test latency, and print running checksums of everything they received; they must match.
Results: no latency → delay 0, 33 ms frames; 30 ms each way → ping 61 ms, delay 1, 33 ms frames; 60 ms each
way → ping 121 ms, delay 2, 33 ms frames; 30 ms each way with a fixed delay 0 → 36 ms frames (the slowdown the
automatic delay avoids). Checksums identical in every run.

Session start: all machines must start from the same files (save, `dr.ini`, key config) and the same seed.
For now copy the host's save folder to the clients by hand; sending it automatically is planned.

## Testing on one PC

`tools/local_test.sh` runs a host and a client in two Wine prefixes with copies of the same save folder.

## Testing without a network: record and replay

`mode=record` stores the local bits of every frame in `dronline_input.bin` and writes a state
fingerprint every 30 frames to `dronline_hash_record.txt`. `mode=replay` plays the same bits back and
writes `dronline_hash_replay.txt`. Load the same save both times, then:

```
python3 tools/compare_hashes.py dronline_hash_record.txt dronline_hash_replay.txt
```

If the logs match, the game is deterministic for that stretch of play, which is exactly what lockstep needs.

## Determinism hazards

`tools/scan_determinism.py` lists them. Chapter 5 (game 0.253 + mod v19):

| Hazard | Uses | Entries |
|---|---|---|
| random calls | 2939 | 660 |
| system clock | 94 | 52 |
| audio state | 58 | 40 |
| fps / delta time | 19 | 11 |
| window / display | 17 | 9 |
| file reads | 20 | 11 |

Random calls are fine as long as every machine consumes the RNG in the same order with the same seed.
Clock, audio and fps reads are the real risks; record/replay shows which ones matter in practice.

## Pitfalls found while testing

- **GameMaker DLL functions with more than 4 arguments must take numbers only.** Otherwise the runner
  refuses to bind the function ("has more than 4 parameters and they are not all numbers") and every
  call silently returns 0. That is why the host address is passed separately (`drnet_set_host`).
- **Extension functions must also be in the FUNC table**, or the compiler emits a variable call and the
  game fails with "Variable drnet_... not set before reading it" (`patcher/utmt/add_extension.csx`).
- **Do not dispatch window messages while blocking.** Running GameMaker's window procedure in the
  middle of a frame deadlocked both games; peeking (`PM_NOREMOVE`) is enough to keep the window alive.
- **A chapter change (`game_change()`) starts a new game process, and the old one exits without Clean Up.**
  The new process inherits the old one's open handles, so a socket bound to the host port stays bound inside the
  new process and hosting again fails until that process ends; the partner gets no goodbye and only notices a
  timeout. Found with GameMaker's own TCP sockets in the Phase 1 spike test (2026-11-23, PR #2). `drnet.c`
  therefore marks its socket as not inheritable. Reconnecting after a chapter change needs its own handling.
- **DELTARUNE fires Game End when it restarts itself at chapter start** (`room_gms_debug_failsafe`
  → `ROOM_INITIALIZE`), so nothing in Game End may close the network socket.
- Under Wine/Proton with two monitors, the game positions its window from the X11 monitor layout, which
  can differ from the Wayland compositor's layout, so windows may open off screen.

## Headless test

`tools/headless_test.sh` starts a host and a client of the patched chapter directly (no launcher) inside
invisible `gamescope --backend headless` instances and prints both `drnet.log` files. Result on Chapter 5
(Proton GE 10-34, one PC): connected at frame 1, 1500 frames in lockstep, no desync.

## Planned: own SOUL and split camera

Both are drawing-only changes, so they must never change game state or consume the shared RNG:

- **Own SOUL**: in battle, draw the local player's heart normally and the other players' hearts with lower
  alpha or an outline. Only the Draw event of the heart objects changes; their positions and collisions stay
  identical on every machine.
- **Split camera**: game logic keeps using the one shared camera (`camerax()`/`cameray()` are read by many
  scripts), so it must stay identical everywhere. The split view is extra rendering on top: when the players
  are further apart than a threshold, each machine additionally draws a second view around its local player.
  Extra views make GameMaker run every Draw event once more per view, and some DELTARUNE Draw events call
  `random()`, so a split decision must be made identically on all machines (it depends only on player
  positions, which are synced) or the extra view must be drawn to a surface without running Draw events.

## Virtual audio clock

Some logic follows the music: the Chapter 5 logo appears on `audio_sound_get_track_position`, rhythm sections
time notes by it, and many scripts wait for `audio_is_playing`. Audio starts with a different latency on every
machine (this caused a 1-pixel desync in the intro), so online every audio state read is answered from the
lockstep frame counter: position = offset + frames since start / 30 × pitch, with pause, resume, stop,
set-position and pitch tracked per sound instance. The real sound still plays normally.

Local-only keys (`Esc` held to quit, `F4` fullscreen) read the real keyboard; they do not change game state.

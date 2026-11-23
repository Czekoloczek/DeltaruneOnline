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

The **mouse** is not part of the virtual input yet. 8 entries in Chapter 5 read it; all but one are editors,
test objects and debug code. The exception is a battle attack: in `obj_attack_blue_flowerbullet` (Step) a flower
bullet explodes as soon as the mouse cursor is over it, so with the cursor in a different place on each screen
the machines would desync. Online, mouse reads must answer the same value everywhere (for example a position far
off screen).

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

**Goodbye.** A game that closes sends a BYE packet with its last inputs: from obj_time's Game End (window
closed, Esc held, `game_end`) and from `dro_game_change` (chapter change), through `drnet_bye`, and from
`drnet_stop`. The others stop waiting for it as soon as they run out of its input, then show "the other player
left the game" and close, instead of freezing for the 10 s timeout. The last inputs inside the BYE let a partner
that is a frame behind still reach the same last frame. Every machine changes chapter in the same frame, so
`dro_game_change` also marks the game as leaving: a goodbye that arrives then shows no message. `game_restart`
fires Game End too, but the game goes on, so `dro_game_restart` suppresses that goodbye, and a player who sends
input newer than their goodbye counts as back. Older input that arrives after the goodbye (sent just before it, or
reordered) does not. With 3 players, a client gets the other client's input only through the host, so once the host
said goodbye, a client counts any missing input as "left". A crash or a killed process sends nothing, so the 10 s
timeout remains for those.

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
For now copy the host's save folder to the clients by hand; sending it automatically is planned. Two files in that
folder change gameplay and are easy to forget when the transfer is automated:

- **`keyconfig_mod.ini`**, the Local Multiplayer settings: `SHARE_INV` (shared inventory), `EASIER_BOSS`,
  `OLD_RHYTHM` (old rhythm game), `PREF_RALSEI`, `P2_FEATHER_INVERT` / `P3_FEATHER_INVERT`. Colours and
  controller numbers are local only. `PLAYER_AMOUNT` does not need copying: online, the player count comes from
  `players` in `dronline.ini`. It is set when the network starts, and the patcher turns the mod's reads of
  `PLAYER_AMOUNT` (new game, loading a save, the config menu) into `dro_ini_player_amount`, which returns the
  session's count. F1–F3 still change it during play; they travel as input, so every machine applies them.
- **`true_config.ini`**, which holds the language (`LANG`). English and Japanese text have different lengths, so
  dialogue would end on different frames. Without the file the game picks the language from the OS locale, so a
  Japanese Windows starts in Japanese.

The chapter select launcher cannot carry session settings: it builds a fresh parameter list for the chapter
(only `launcher`, `switch_<n>` and `returning_<n>`), so anything passed to `DELTARUNE.exe` is dropped. That is
why the settings live in `dronline.ini`.

## Testing on one PC

`tools/local_test.sh` runs a host and a client in two Wine prefixes with copies of the same save folder.

On Windows both copies share one save folder (`%LOCALAPPDATA%\DELTARUNE`), so they would read the same
`dronline.ini`. A copy started with the environment variable `DRONLINE_ROLE=<name>` (letters and digits) reads
`dronline_<name>.ini` instead, if that file exists, and `drnet.dll` then logs to `drnet_<name>.log`.
`tools\local_test.bat "<game folder>"` creates `dronline_host.ini` and `dronline_client.ini` (client connects to
127.0.0.1) and starts both copies; choose Chapter 5 in each window. Without the variable, or without the
matching file, a copy uses `dronline.ini` and `drnet.log` as before. Windows passes environment variables on to
child processes, so the role should survive the launcher's chapter change too.

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
| mouse | 42 | 8 |

Random calls are fine as long as every machine consumes the RNG in the same order with the same seed.
Clock, audio and fps reads are the real risks; record/replay shows which ones matter in practice.

**System clock.** Audio state already goes through the virtual audio clock, but clock reads do not. Of the
52 clock entries in Chapter 5, 30 are Draw events, which only change the picture. Of the other 22, `scr_save`
only writes the date into the save file, and `scr_screenspace` only uses the clock as a cache key for the camera
position and is called from Draw events alone. The remaining **20 are game logic** and need a frame-based clock
(`frames × 1000 / 30` ms): `scr_wave`, `obj_caterpillar_dash`, `obj_climb_susie`,
`obj_cliff_petalwarp` (and its `_old` copy), `obj_dw_flowerydash`, `obj_dw_post_fountain_close`,
`obj_flowery_runner`, `obj_flowery_shooter`, `obj_towery_flowery`, `obj_rotating_bullet_new`, `obj_waterpit`, and the platformer objects `obj_plat_coin`,
`obj_plat_enm_yellow_miniboss`, `obj_plat_finalflowery`, `obj_plat_hanging_key`, `obj_plat_pinatabell`,
`obj_plat_stringed_object`. The rhythm game times itself with `delta_time`, counted under fps / delta time.

**Other chapters** (same scanner, game 0.253 + mod v19, uses / entries):

| Hazard | Ch 1 | Ch 2 | Ch 3 | Ch 4 |
|---|---|---|---|---|
| random calls | 416 / 113 | 1951 / 524 | 2507 / 660 | 2338 / 519 |
| system clock | 1 / 1 | 3 / 3 | 23 / 13 | 133 / 63 |
| (clock outside Draw) | 1 | 3 | 7 | 34 |
| audio state | 2 / 2 | 80 / 45 | 50 / 33 | 74 / 47 |
| fps / delta time | 2 / 2 | 3 / 3 | 14 / 7 | 21 / 13 |
| window / display | 14 / 9 | 15 / 9 | 22 / 13 | 20 / 12 |
| file reads | 10 / 8 | 15 / 12 | 26 / 19 | 19 / 14 |
| mouse | 0 / 0 | 15 / 6 | 34 / 9 | 36 / 8 |

All five chapters share the same input design (`obj_time` Begin Step, slots 0–9 / 200–205 / 300–305), so the
virtual input layer carries over; the work per chapter is mostly these hazards. Chapter 4 has the most clock
reads in game logic.

## Pitfalls found while testing

- **GameMaker DLL functions with more than 4 arguments must take numbers only.** Otherwise the runner
  refuses to bind the function ("has more than 4 parameters and they are not all numbers") and every
  call silently returns 0. That is why the host address is passed separately (`drnet_set_host`).
- **Extension functions must also be in the FUNC table**, or the compiler emits a variable call and the
  game fails with "Variable drnet_... not set before reading it" (`patcher/utmt/add_extension.csx`).
- **Do not dispatch window messages while blocking.** Running GameMaker's window procedure in the
  middle of a frame deadlocked both games; peeking (`PM_NOREMOVE`) is enough to keep the window alive.
- **DELTARUNE fires Game End when it restarts itself at chapter start** (`room_gms_debug_failsafe`
  → `ROOM_INITIALIZE`), so nothing in Game End may close the network socket.
- Under Wine/Proton with two monitors, the game positions its window from the X11 monitor layout, which
  can differ from the Wayland compositor's layout, so windows may open off screen.
- **The socket must not be inheritable.** A chapter change (`game_change()`) starts a new game process that
  inherits the old one's handles, and the old process exits without Clean Up. An inherited socket kept the host
  port bound inside the new process, so hosting again failed. `drnet.c` creates its socket with
  `WSA_FLAG_NO_HANDLE_INHERIT` (making it non-inheritable afterwards passed the Windows test, 2026-11-23).
- **No networking at process exit.** A goodbye sent from `DllMain` (`DLL_PROCESS_DETACH`) was logged as sent
  but never arrived (Windows, 2026-11-23), so it is sent from Game End and the chapter change instead, while the
  game still runs.

## Playing over WireGuard

WireGuard puts both PCs on one private network, so the game works as on a LAN:

- Only the PC that runs the **WireGuard server** needs a forwarded port on its router: WireGuard's own UDP port
  (51820 by default). The game's port needs no forwarding.
- In `dronline.ini` on the clients, `host=` is the game host's **WireGuard address** (for example `10.8.0.1`),
  not its public internet address.
- Windows Firewall often classes the WireGuard network as *Public* and blocks incoming traffic. The game host
  needs an inbound rule for **UDP 7845** (or for `DELTARUNE.exe`) on that network. With the star topology only
  the host needs it.
- Packets are small (16 frames of input per packet), so WireGuard's lower MTU is no problem.
- Any PC can be the game host; it does not have to be the one running the WireGuard server.

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

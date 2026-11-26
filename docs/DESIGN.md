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

The **mouse** is not sent over the network, because DELTARUNE does not use it for multiplayer controls, yet 8
entries in Chapter 5 read it (7 editors, test objects and debug code, plus one battle attack: in
`obj_attack_blue_flowerbullet` (Step) a flower bullet explodes as soon as the mouse cursor is over it, so
different cursor positions would desync the machines). The patcher therefore rewrites mouse reads (`mouse_x`,
`mouse_y`, `mouse_check_button*`, `mouse_wheel_*`, `window_mouse_get_x/y`, `device_mouse_*`) to `dro_*` functions:
online (and in `solo`/`record`/`replay`) they return a constant off-screen position (`-100000`) and `false` for
buttons and wheel; offline they pass through to the real mouse. `window_mouse_get_delta_x/y` are left alone:
DELTARUNE's runner doesn't have them, and a stand-in that calls a function the runner lacks stops the game while
it loads ("Unable to find function", seen in the first build of this change).

## Virtual input layer (`gml/scr_dronline.gml`)

1. Every machine reads **its own** keyboard and gamepad and turns them into **action bits** of its local
   player, using its own key config (bit 0 down, 1 right, 2 up, 3 left, 4 confirm, 5 cancel, 6 menu,
   7 teleport, 8–10 F1–F3).
2. The bits of all players are combined into a **virtual keyboard**: player *p*'s action *a* presses
   `global.input_k[base_p + a]`. All virtual gamepads report "disconnected", so the game and the mod
   only ever see the virtual keyboard.
3. The patcher rewrites every built-in input read to the `dro_*` functions, which answer from the
   virtual keyboard (or pass through to the real device when the online mode is off).
4. `randomize()` / `randomise()` becomes `dro_randomize()`, which seeds every machine with the same seed and, like
   `randomize()`, returns it: the game may use the value (Chapter 5's canopy shadows seed the generator with it again).
   Every `dro_*` stand-in for an engine function that returns a value must return one online too;
   `tests/test_consistency.py` checks that.

Because every machine feeds the same bits into the same game state, every machine computes the same frame.

## Lockstep

Each frame *F*, a machine sends its bits for frame *F + D* and waits until it has every player's bits
for frame *F*. Waiting has to block inside the frame, which GameMaker's asynchronous networking cannot do, so
the native DLL `native/drnet.c` (Windows build, also under Proton) handles UDP, resends and the blocking wait.
A network thread receives packets the moment they arrive, so the ping is measured exactly and the host relays
input without waiting for its own next frame. Star topology: clients talk only to the host, which relays
everyone's input, so only the host needs an open UDP port (default 7845). Every packet carries the sender's
last 16 frames, so a lost packet is covered by the next one. During the handshake (`HELLO` / `REJECT`, protocol 4),
`drnet.dll` hashes the running chapter's `data.win` (from `-game <path>` or next to `drnet.dll`) with SHA-256 —
refusing to start if `data.win` cannot be read — and the host rejects clients with a build, protocol or player-count
mismatch (showing both SHA-256 hash prefixes on a `data.win` mismatch) while keeping the lobby open for valid peers
so an incompatible or stray packet cannot abort a session. All players must update `drnet.dll` together: mixing
protocol 3 and protocol 4 peers can still hit the connection timeout on the protocol-3 side. State fingerprints are
exchanged every 30 frames to detect desyncs.

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
- The hooks are all or nothing. Hits may only follow the owner's view if the owner's heart is drawn there, so
  the patcher also leaves them off when obj_heart has no Draw event, or its Draw contains `exit` or `return`
  (which would skip the call that puts the shared position back). It then prints why after "own SOUL hooks",
  and every heart moves and is hit with the delay. Chapter 5's Draw is wrapped.

The price: the other players' hearts on your screen lag behind what they see by their delay, so now and then a
bullet seems to pass through another player's heart without a hit, or to hit it from a distance. Every hit
follows what the dodging player saw.

**Other players' SOULs are faint.** Several hearts of the same shape on one screen get mixed up, so each machine
draws only its own player's SOUL fully; the others are drawn at `other_souls` opacity from `dronline.ini` (default
0.1, 0 hides them, 1 draws them as the game does). The setting is local: players may choose different values, and
they don't need to match. It is drawing only. The patcher multiplies the alpha argument of the calls that draw a SOUL
by a factor that is 1 offline, in solo and for the local player's own heart, and adds, removes or reorders no
statement, so every heart still moves, dodges, grazes and is hit on every machine and the state hash is unchanged
(obj_heart's Draw advances the animation of hearts 2 and 3 itself, and obj_grazebox counts down its timer there:
both keep running). The calls it changes:

- obj_heart's Draw, which draws all hearts of the Local Multiplayer mod (heart *k* at x/y, x2/y2, x3/y3): a call
  belongs to the heart whose coordinates its position names. That covers the heart, the invulnerability animation
  (a sprite animation, no blink), the death break and the yellow and orange extras. A position that names no heart
  (an orange tail at `tx, ty`) goes to the nearest heart, with ties going to the local player; a position that
  contains a call is not evaluated twice and stays as it is. `draw_self()` becomes `dronline_draw_self(factor)`.
  In the purple SOUL's mode 7 only the heart at x/y is drawn, for whoever plays the turn, so it stays full.
- The purple SOUL draws the hearts a second time inside `with (obj_heart)` in obj_purplecontrols' Draw: the same
  rule, only inside that block.
- Objects that hold one player's SOUL and say whose in `controls`: obj_grazebox (graze sparkles, 1–3),
  obj_orangeheart (the orange chase SOUL with its afterimages, 0–2), obj_date_heart (0–2) and obj_heart_slime
  (0–2; it draws into a surface, so only the surface on the screen is dimmed). -1 means no owner and stays full.

Not covered yet: the heart shards after a death (plain obj_marker instances without an owner, whose alpha is game
state), obj_dashpath (the mod draws it at heart 1 for every player) and the mod's outline in obj_fusebomb_big
(also always at heart 1). The patcher prints how many calls it changed per object after "other players' SOULs
drawn faint".

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

## Automated tests (no game needed)

`bash tools/verify.sh` runs all of them in one go, with the lockstep test in four variants: 0 and 30 ms with 2
players, 3 players, and 3 players of whom the last joins 12 s late, and then the Agent API test. Both need Wine on
Linux (Git Bash on Windows runs them natively); without Wine, or with `SKIP_LOCKSTEP=1`, `verify.sh` skips them,
says so, and still ends with OK, so read its output.
`bash tools/setup_env.sh` installs the tools with apt, dnf or pacman, and only warns when no Wine package is
available. Cloud agents such as CodeRabbit run `setup_env.sh` as their setup script and are told to check
changes with `verify.sh`.

They also run on every pull request and push to main (`.github/workflows/tests.yml`):

- `python3 -m unittest discover -s tests -v` (standard library only):
  - `tests/test_patcher.py`: the rewrite rules (word boundaries, idempotence, only `PLAYER_AMOUNT` among the mod's
    settings), the obj_heart hooks, including "all or nothing" when the game's code looks different, and the faint
    SOULs of the other players (which heart a draw belongs to, nothing but the alpha changes). The GML snippets are
    written for the tests and only imitate the game's code.
  - `tests/test_consistency.py`: every function the patcher points the game at exists in `gml/scr_dronline.gml`,
    and every `drnet_*` call is exported by `native/drnet.c` and registered in `add_extension.csx` with the same
    types, with no function of more than 4 arguments taking a string (the pitfalls below). Every option the GML
    passes to `drnet_option` is one `drnet.c` knows, and every `dronline.ini` key the GML reads is in
    `config/dronline.ini.example` and in the settings comment of `gml/scr_dronline.gml`.
- `sh native/build.sh && sh native/test_pair.sh [lag_ms] [frames] [players] [late_s]`: runs `test_drnet.exe` as a
  host and its clients and fails unless every side ends with the same checksum of all received input and no
  desync. With `late_s`, the last client starts that many seconds after the others. On Linux it uses Wine
  (`WINE=…/GE-Proton…/files/bin/wine` tests Proton's Wine), on Windows (Git Bash, `CC=gcc`) it runs natively. CI
  runs it on Linux under Wine and on a real Windows machine, each with 0, 30 and 60 ms test latency, with 3
  players, and with a third player who joins 12 s late.
- `sh native/test_handshake.sh`: checks that peers with matching `-game` files connect with identical checksums,
  while mismatched `data.win` hashes (with both SHA-256 prefixes) and mismatched player counts are refused promptly
  without aborting the host or waiting compatible peers, and a missing `data.win` (even on both sides) fails on
  start (Wine on Linux, native on Windows).
- `python3 native/test_agent.py [solo] [long] [online]`: the Agent API of the DLL, with `test_agent.exe` standing
  in for the game (see "Agent API" below). About a minute; CI runs it on Linux under Wine and on Windows.
  `tests/test_agent_client.py`, among the unit tests, covers `tools/agent_client.py` without a game or a network
  and checks that its limits and button names are the DLL's. `tests/test_deepseek_player.py` covers
  `tools/deepseek_player.py` against a small game written in Python and a fake DeepSeek API (no key, no
  network), and `test_agent.py solo` runs it once more against the DLL with its fake model.

Tests that need the game and the mod (the headless test, record/replay, battle tests) stay local, because those
files must not be in the repository or in CI.

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
The script fails when they differ and also when the replay log is shorter than the record log, since a replay
that stopped early proves nothing. A longer replay is fine: it goes on without input after the recording ends.
Game End closes the log, and DELTARUNE fires Game End when it restarts itself at chapter start; the next frame
opens the log again, so both logs cover the whole run.

## Scripted input: unattended tests and replays

For tests with nobody at the keys, each machine can take its own player's input from a file instead of the
keyboard and gamepad. Settings in `[online]` (host, client and solo):

- `script=<file>`: one little-endian u16 of action bits per input frame (the layout of `dronline_local_bits`), the
  same format `mode=record` writes. Esc and F4 still read the real keyboard. Online, an input frame is an
  exchanged frame, so the time spent waiting for the others does not use up the script.
- `script_end=idle|keys|quit`: after the last frame, no input (default), the real keyboard and gamepad again, or
  closing the game the normal way, so the others see "the other player left the game" (test G4 without a person).
- `script_record=<file>`: writes the input this machine used, frame by frame, in the same format. An existing file
  is kept and the recording goes to `<file>.2`, `<file>.3`, ..., because a chapter change starts a new game process.
  Copies on one PC share the save folder, so with `DRONLINE_ROLE` the role goes into the name
  (`dronline_rec.bin` becomes `dronline_rec_host.bin`) and two copies never write the same file.

`tools/input_script.py compile <script.txt> <out.bin>` turns a text script into that file
(`wait 3s`, `tap confirm 2 15`, `20 right`, `10 up+cancel`; see the tool's help), `dump` turns a recording back into
text. To replay a two-player session: record it with the same `delay=<frames>` on every machine (`delay=auto`
picks the delay from the ping, and a different delay moves every input), then give each machine its own
recording as `script=` with that delay and the same saves and seed. The status line shows `script n/total` and
`REC`, and `drnet.log` notes when the script starts and ends.

A script covers one launch of the chapter: after a chapter change the new process starts it from the top again.
The launcher itself is not patched, so a test without people starts Chapter 5 directly, in `chapter5_windows`:
`DELTARUNE.exe -game data.win launcher`. The `launcher` parameter matters: without it the chapter looks for its
music in `chapter5_windows/mus/`, the music stream fails, and the Chapter 5 logo (which follows the music
position) never finishes. Online that is a desync as soon as the other machine's logo moves on (two-PC idle test,
frame 90). A sound that fails to play online is logged in `drnet.log`, and the first 2400 frames log the parts of
every state hash (room, instance count, summed x and y, plot), so the first differing checkpoint shows what differs.

## Agent API

`agent=<port>` in `[online]` (host, client and solo) lets a program on the same PC, such as an AI agent, read the
game state as JSON and send input for the local player. The protocol, the keys of the state and the security
notes are in [agent-api.md](agent-api.md); `tools/agent_client.py` is a client for the command line and a module,
and `tools/deepseek_player.py` lets a DeepSeek model play toward a goal given in words.
**In the game** it has been tried in chapter 5 in `mode=solo` only; not yet online or across a chapter change
(the status at the top of agent-api.md says what was seen).

- **The DLL holds the protocol.** `drnet.dll` listens on 127.0.0.1 only, on a TCP socket of its own (the lockstep
  UDP socket is open to the network on the host and does not exist in `mode=solo`). A thread accepts one
  connection, reads the JSON lines and parses them; the input queue, the limits and the replies live in the DLL
  too. The thread never touches the game. Keeping all of that out of the GML means it can be tested without the
  game, and the GML that cannot be tested here stays small.
- **The game side is three calls per frame.** `dronline_step` starts with `drnet_agent_frame()`: the DLL runs the commands
  that came in. When a reply needs the game state (a `state` command, or an input that is done), it returns 1 and
  the GML hands the state over with `drnet_agent_state(json)`. `dronline_script_frame` adds
  `drnet_agent_bits()`, the agent's buttons for this input frame, to the local player's own, before
  `script_record` writes the frame. From there they travel like key presses: through `drnet_exchange` when online,
  so the lockstep and the desync detection are unchanged.
- **The state code only reads.** The agent runs on one machine, and whatever its code changed in the game there
  would be a desync: no random calls, no instances, data structures, buffers or surfaces created (ids are plain
  numbers and would shift), no game variable written, no events run. The JSON is built with plain string
  functions instead of structs and `json_stringify`, to stay with what UndertaleModTool's compiler is known to
  take. Game variables are read by name (`variable_global_exists`,
  `variable_instance_exists`), so a chapter without one of them gives `null` instead of an error.
- **When `done` is sent.** An input is done when the game has applied its last frame. The DLL knows the frame:
  `assign()` notes in `sample_frame` where the sample of the last `drnet_exchange` went, up to `delay` frames
  ahead. `done` goes out at the start of the first frame after it, so its state shows what the input did.
- **A changing delay.** With `delay=auto`, `assign()` holds a sample back when the delay shrinks (it goes out merged
  with the next one) and repeats it when the delay grows. Two taps one frame apart could become one press that
  way. `assign()` notes in `sample_put` how many frames the last sample went into, and `drnet_agent_bits()` gives
  the same buttons again after a held sample and leaves out the repeated frames of the step being played. So no
  press is lost or merged; a step can only get longer, by the frames the delay grew, when it grows during the
  step's last frames. A held sample has no frame yet, so its `done` waits until `assign()` has written it.
- **Chapter change.** The new game process listens on the same port. The listener is exclusive
  (`SO_EXCLUSIVEADDRUSE`), and Windows keeps such a port busy while a connection accepted on it still lives. So
  `dro_game_change` calls `drnet_agent_stop()`, which closes the connection with a reset and the port, and a
  game that finds the port busy at its start tries again every second (`dronline_agent_poll`).
- **Who may connect.** Any program on the PC: there is no password, which is why the key is off by default. A
  browser is kept out by the first line: a connection that does not start with a JSON object, as every HTTP
  request does not, gets one error and is closed. The socket is not inheritable (see "The socket must not be
  inheritable" below), so the process of the next chapter can listen on the same port.

`native/test_agent.py` tests the DLL side without the game, on Linux under Wine and on Windows: `test_agent.exe`
stands in for the game, calls the DLL in the order the GML does and moves a point with the input it gets. The
script checks the replies and their order, about 45 kinds of refused command, the limits, `clear`, a second
connection, input that ends with its connection, an HTTP request, the port closed and opened again while the
agent still holds its old connection, replies of 300 KB, and, with a host and a client in lockstep, that `done`
shows exactly the frames of the input, also while the automatic delay grows and shrinks.

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

**System clock.** Audio state already goes through the virtual audio clock, and the monotonic timers used in
gameplay go through the lockstep frame clock online (and the real built-ins offline):

- `get_timer()` (5 entries, including `obj_spin_petal`'s Draw which sets `x` and desynced the Chapter 5 garden on
  the first frame) → `dro_get_timer()`, returning `frames × 1000000 / 30` µs.
- `current_time` → `dro_current_time()`, returning `frames × 1000 / 30` ms (tested online in a two-player battle,
  where `obj_heart`'s Draw reads it, without a desync; the game-logic entries below are not tested in the game yet).
  That covers the 20 Chapter 5 game-logic entries (`scr_wave`, `obj_caterpillar_dash`, `obj_climb_susie`,
  `obj_cliff_petalwarp` and `_old`, `obj_dw_flowerydash`, `obj_dw_post_fountain_close`, `obj_flowery_runner`,
  `obj_flowery_shooter`, `obj_towery_flowery`, `obj_rotating_bullet_new`, `obj_waterpit`, `obj_plat_coin`,
  `obj_plat_enm_yellow_miniboss`, `obj_plat_finalflowery`, `obj_plat_hanging_key`, `obj_plat_pinatabell`,
  `obj_plat_stringed_object`), as well as Draw animations and `scr_screenspace`'s per-frame cache key (which still
  changes once every lockstep frame). `date_current_datetime()` is left untouched so `scr_save` writes the real date.
- `delta_time` → `dro_delta_time()`, returning `1000000 / 30` µs (one fixed 30 fps frame, matching `dro_get_timer()`
  and the virtual audio clock; covers the rhythm game, not tested in the game yet).

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
- **Same sources, different `data.win`.** `import_code.csx` compiled the files in `Directory.GetFiles` order, which is
  the file system's own order. The import order decides where new strings, functions and variables land, so the same
  commit, input and UTMT gave `3ea8870e…` in one work folder and `64eeeb3f…` in another (2026-11-23). The import is
  now sorted (ordinal). The UTMT version changes the result too (`491b656` gave `96bbf214…`), so every player must
  build with the same UTMT commit, or use one shared build.

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

## Own camera and arrows

The faint SOULs of the other players are done (see "Own SOUL" above). The own camera and the arrows are
drawing-only changes too, so they must never change game state or consume the shared RNG:

- **Own camera** (a draft, `camera=own`, default `shared`; it replaces the split screen planned before): online
  every player has a screen of their own, so instead of splitting one screen, each machine shows the view around
  its own character once that character leaves the shared view, and glides back when it returns. Game logic
  keeps the one shared camera: `camerax()`/`cameray()` are read in about 345 Step, Alarm and Create events and
  38 scripts of Chapter 5, so the camera they read must be identical everywhere. The own view therefore exists
  only while the game draws: obj_time's Pre-Draw moves the view (the game itself moves the view in Pre-Draw for
  its sun shadows), and obj_time's Post-Draw puts the shared position back before the next Step. There is still
  one view, so every Draw event runs once per frame as before. A second view (a real split screen) would run
  every Draw event twice per frame: about 400 of Chapter 5's 979 Draw events change variables (animation
  counters run twice as fast) and some call `random()`, so all machines would have to split in the same frame.
  **The risk**: Draw events between Pre-Draw and Post-Draw read the own camera. About 30 of Chapter 5's Draw
  events store something computed from the camera, and 4 of them move an instance. Where such a value feeds the
  game logic, the machines go out of sync: each of them must be checked and, where needed, rewritten by the
  patcher to read the shared camera (`global.dro_cam_sx`/`dro_cam_sy` while the view is moved). A desync shows in
  the status line and `tools/diff_state.py` names the object. **Not tested in the game yet.** obj_time has no
  Pre-Draw in Chapter 5; the importer creates it (checked in a build).
- **Arrows** (`arrows=1`, the default): in the overworld an arrow at the edge of the screen points to every other
  player whose character is out of the view that is drawn. Player 1 is Kris (`obj_mainchara`); in the Local
  Multiplayer mod players 2 and 3 steer the party member (`obj_caterpillarchara`) whose `controls` is 1 or 2. The
  arrows are drawn in obj_time's Draw GUI and only read instances. Not tested in the game yet.

## Virtual audio clock

Some logic follows the music: the Chapter 5 logo appears on `audio_sound_get_track_position`, rhythm sections
time notes by it, and many scripts wait for `audio_is_playing`. Audio starts with a different latency on every
machine (this caused a 1-pixel desync in the intro), so online every audio state read is answered from the
lockstep frame counter: position = offset + frames since start / 30 × pitch, with pause, resume, stop,
set-position and pitch tracked per sound instance. The real sound still plays normally. A sound that ends on its
own is dropped from that table 10 s later (checked every 300 frames, the same on every machine), so the table
does not grow with every text sound and step of a long session.

Local-only keys (`Esc` held to quit, `F4` fullscreen) read the real keyboard; they do not change game state.

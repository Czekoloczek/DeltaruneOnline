# Agent API, v1: spec for the DeepSeek draft

Status: request for a first draft (2026-11-23). The code that answers it is an unreviewed draft written by
DeepSeek V4 Pro; Czekoloczek decides what happens to it.

## Goal

An AI agent (for example Claude Code on the player's PC) should be able to play DELTARUNE from data instead of
screenshots: read the game state as JSON, and send input such as "hold right for 30 frames, then press confirm".
v1 covers the **overworld and dialogue only**. Battles are out of scope (the agent may read `battle: true` and
stop, nothing more).

## Shape

- **Transport.** `drnet.dll` opens a separate **TCP** listener on **127.0.0.1 only** (never 0.0.0.0), port from
  `agent=<port>` in `[online]` of `dronline.ini` (no key or `agent=0`: off, nothing is opened). It is not the
  lockstep UDP socket: that one is open to the network on the host and does not exist in `mode=solo`. One agent
  connection at a time; a second one is refused or replaces the first (say which). Messages are JSON objects, one
  per line (`\n`), UTF-8, both directions. The listener runs on its own thread, created non-inheritable like the
  lockstep socket (`WSA_FLAG_NO_HANDLE_INHERIT`, see "The socket must not be inheritable" in docs/DESIGN.md), so a
  chapter change (new process) can listen on the same port again. The agent reconnects after a chapter change.
- **Modes.** Works in `solo`, `host` and `client`. In `mode=off` nothing changes (the patched game must behave like
  the unpatched one). `record`/`replay` may ignore it.
- **Input path.** The agent's input becomes the **local player's action bits** (same bit layout as
  `dronline_local_bits`: bit 0 down, 1 right, 2 up, 3 left, 4 confirm, 5 cancel, 6 menu; the agent may use only
  these 7), OR'ed with the real keyboard/gamepad bits so a person can still step in. It goes in at the same place as
  `script=` input (`dronline_script_frame`), once per input frame (every frame offline, every exchanged frame
  online), and before `script_record` writes the frame, so a recording includes the agent's input. Online it then
  travels through `drnet_exchange` like any other input, so lockstep and desync detection are unchanged.
- **Commands (agent -> game).** Every command may carry `"id"` (number or string), echoed in its replies.
  - `{"cmd":"state"}` -> `{"type":"state","id":...,"state":{...}}` built in the next game frame.
  - `{"cmd":"input","steps":[{"buttons":["right"],"frames":30},{"buttons":[],"frames":2},{"buttons":["confirm"],"frames":2}]}`:
    queue the steps (each holds those buttons for that many input frames; `[]` = nothing held). Reply at once with
    `{"type":"queued","id":...,"frames":<total>}`. When the last frame has been **applied by the game** (online that
    is `drnet_delay()` frames after it was sent), send `{"type":"done","id":...,"state":{...}}`. Several input
    commands queue up in order. Limits: at most 60 steps per command, at most 3600 frames per step, at most 10
    queued commands; over a limit -> error, nothing queued.
  - `{"cmd":"clear"}`: drop all queued input; reply `{"type":"cleared","id":...}`; queued commands that never ran
    get no `done`.
  - Anything else, or a line that is not valid JSON -> `{"type":"error","id":...,"error":"<text>"}`. Never crash
    or stall the game on bad input; lines over 64 KB are dropped with an error.
- **State JSON.** Built in GML on demand only (a `state` request or a `done`), never every frame. Keys:
  - `frame` (`global.dro_frame`), `chapter` (`global.chapter`), `room` (`room_get_name(room)`), `mode`,
    `player` (local player 1-3), `players`, `online` (bool), `desync` (`global.dro_desync`).
  - `interact` (`global.interact`: 0 = free to move; 1 dialogue/cutscene; 6 dark world menu open; other values
    raw), `menuno` (`global.menuno`, -1 = no menu), `submenu` (`global.submenu`), `battle` (`global.fighting == 1`),
    `can_move` (`interact == 0 && !battle`). Read every global with `variable_global_exists` first; missing ->
    `null`.
  - `players_pos`: list of `{"player":p,"object":name,"name":char_name,"x","y","facing"}`. Player 1 is
    `obj_mainchara` (facing `global.facing`: 0 down, 1 right, 2 up, 3 left). In the Local Multiplayer mod, player 2
    is the `obj_caterpillarchara` instance with `controls == 1` (facing `global.facing2`) and player 3 the one with
    `controls == 2` (facing `global.facing3`); its `name` variable is "susie", "ralsei" or "noelle". Missing
    instance -> leave that player out.
  - `dialogue`: list of the `obj_writer` instances: `raw` (`mystring`), `text` (cleaned, below), `shown`
    (characters typed so far: `pos - 1`, capped at the length), `length` (`string_length(mystring)`), `waiting`
    (`halt != 0`: the box waits for confirm), `x`, `y`. Cleaning `mystring`: `&` and `\n` -> newline;
    `` ` `` keeps the next character literally; drop `/`, `%`, `|`; drop `^` plus the one character after it; drop
    `\` plus the two characters after it (`\E2`, `\cR`, `\M1`, ...).
  - `choice`: `null`, or, while an `obj_choicer_neo` exists: `options` (`global.choicemsg[0..choicetotal]`, with
    `string_hash_to_newline`), `selected` (`mychoice`, -1 = none), `can_choose` (`canchoose == 1`). Directions pick
    an option: 0 left, 1 right, 2 up, 3 down; then confirm.
  - `camera`: `x`, `y`, `w`, `h` of `view_camera[0]` (built-in `camera_get_view_*`).
  - `nearby`: up to 30 instances within 240 px of player 1, nearest first: `object` (`object_get_name`), `x`, `y`,
    `solid`, `visible`. Skip instances without a sprite and the players themselves.
  JSON is built with plain string functions and a small escape helper (quotes, backslash, control characters), not
  `json_stringify`, so it compiles with UndertaleModTool's compiler.

## Rules that must hold (lockstep safety)

The agent runs on one machine only. Anything it does there that the other machines do not do is a desync, so the
state code must be **read-only**: no `random*` calls, no instance creation, no `ds_*`/buffer/surface creation (ids
are plain numbers and would shift), no writes to any game variable or global except its own `global.dro_agent_*`,
no `with` blocks that assign, no event calls. Arrays and strings only. The DLL thread never touches game state; the
GML side pulls from it once per frame.

GameMaker DLL pitfalls from docs/DESIGN.md: a function with more than 4 arguments must take numbers only; strings
are `const char *` in, `const char *` out; every new `drnet_*` export must also be registered in
`patcher/utmt/add_extension.csx` with the same types (tests/test_consistency.py checks this). Keep `drnet.dll`
free of new libraries (MinGW, `-lws2_32 -luser32` only) and C89-friendly like the rest of `drnet.c`. A small
hand-written JSON reader for the command subset is enough.

## Also wanted

- `tools/agent_client.py` (standard library only): `state` prints the state; `do "<steps>"` sends steps written in
  the `tools/input_script.py` text syntax (import its parser, e.g. `"20 right; tap confirm 2 15"`), waits for
  `done` and prints the state; `--port`, `--host` (default 127.0.0.1), `--timeout`; usable as a module (a small
  class) by an agent. It must work from Linux against the game under Proton (Wine sockets are host sockets).
- `tests/test_agent_client.py`: unit tests without a game or network (step conversion, JSON line framing).
- Docs in the same change (CLAUDE.md): the `agent=` key in `config/dronline.ini.example` and in the settings
  comment at the top of `gml/scr_dronline.gml`; a short "Agent API" section in `docs/DESIGN.md`; one line in
  `README.md`. Mark it everywhere as an untested draft.

## Where the code goes

Add the code to the existing files (`native/drnet.c`, `gml/scr_dronline.gml`, `patcher/utmt/add_extension.csx`),
as new sections at their ends where possible, so the build, the patcher and tests/test_consistency.py keep working
unchanged. Keep the existing code as it is apart from the few lines that call the new code. Nothing from the game
or the mod goes into the repository.

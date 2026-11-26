# Agent API

An AI agent (for example Claude Code on the player's PC) can play DELTARUNE from data instead of screenshots: it
reads the game state as JSON and sends input such as "hold right for 30 frames, then press confirm". Version 1
covers the **overworld and dialogue**. In a battle the agent can only see `"battle": true`.

> **Status: tried in the game, in `mode=solo` only.** In chapter 5 (Linux, under Proton) an agent connected, read
> the state and played: the file select, loading a save, walking, talking to a save point, the Dark World menu
> and one of its pages, a save point's menu up to the question whether to overwrite a file. `room`, player 1 in
> `players_pos`, `nearby`, `dialogue`, `interact`, `menuno` and `save_menu` matched the screen. Not tried in the
> game yet: an agent in an online game, a chapter change with an agent connected, a choice box (`choice`),
> players 2 and 3 in `players_pos`, and saving itself (`save_menu` after a file was written). The names of the
> game's objects and variables for those were looked up in the game, not seen working. The network side
> (protocol, input queue, timing) is tested without the game, on Linux under Wine and on Windows
> (`native/test_agent.py`).

## Turning it on

`agent=<port>` in `[online]` of `dronline.ini`, in `mode=solo`, `host` or `client` (not `record`/`replay`). No
key, or `agent=0`: nothing is opened, and the game behaves as before.

`drnet.dll` then listens for TCP connections on **127.0.0.1:\<port\>**, this computer only. The agent is a program
on the same PC; under Proton it can be a Linux program, because Wine's sockets are the host's. One agent at a time:
while one is connected, another connection is answered with `another agent is connected` and closed.

A chapter change starts a new game process. The game closes the agent's connection and the port just before it,
and the new process opens the port again, so the agent connects again and may have to try for a few seconds. If
the port is still busy when the game starts, the game tries again every second.

```
python3 tools/agent_client.py state
python3 tools/agent_client.py do "20 right; tap confirm 2 15"
```

`tools/agent_client.py` is a client for the command line and a small module for agents written in Python
(standard library only; `python3 tools/agent_client.py --help`). Its steps use the text of
`tools/input_script.py`. Scripts of any length work: the client cuts them into commands and keeps the game's
queue filled.

## DeepSeek as a player

`tools/deepseek_player.py` lets a DeepSeek model play through this API, toward a goal given in plain words:

```
python3 tools/deepseek_player.py "walk to the save point and open its menu" --until save_menu
```

Every turn it reads the state, sends DeepSeek a few lines about it (room, where the player is, the dialogue, a
choice, the save point menu when the state has it, the nearest objects with their offset from the player, the
walking speed measured so far), the goal, what is done so far and the last 30 turns, and plays the steps the model
answers with (the text of `agent_client.py`, at most `--max-frames` frames, default 150 = 5 s; an answer of more
than 3600 frames is refused before it is built).

- **What it knows about the game.** The system prompt carries a guide, `tools/deepseek_guide.md`: what each button
  does, that "the menu" in a goal means the Dark World menu and not a save point's, how that menu and a save point
  work, dialogue and choices, rooms and exits, battles. `--guide <file>` sends another one, `--guide none` none.
  The guide is part of the system prompt, which stays the same every turn, so after the first call it is billed
  at the cache-hit price. The state's numbers come in words too: `interact=5: the Dark World menu is open`, the
  save point menu as "the list of file slots, cursor on file slot 2", `obj_savepoint (save point)`, and `menuno`
  only while the Dark World menu is open (it keeps an old value otherwise).
- **What it sees.** With `deepseek-flash` every turn comes with a screenshot of the game, scaled to the game's own
  640x480 (about 206 image tokens by DeepSeek's calculator, so about 0.00006 USD a turn at the peak price; DeepSeek
  counts at most 1024 tokens an image, and an image below about 544x544 is scaled up, so a smaller one saves
  nothing). The camera follows the player, so a picture is only good for its turn: the model can put `look` among
  its steps ("60 right; look; 40 up") to stop there and be asked again with a new picture. On Windows the player
  captures the game's window itself (`tools/screen_capture.py`, GDI through ctypes: the client area, cut to the
  4:3 picture when fullscreen). It uses PrintWindow, which asks the game's window for its own pixels, so a window
  over the game is never in the picture. If PrintWindow gives black, that turn has no picture, unless
  `--screen-grab` is given: then the game's part of the screen is copied, but only if no visible window of another
  program overlaps the game just before and just after the copy (such a copy would send that window to DeepSeek; a
  window that comes and goes within those few milliseconds is not seen). Elsewhere `--screenshot-cmd`
  gives a command that prints the image, for example under Proton on X11/XWayland
  `import -window "$(xdotool search --name '^DELTARUNE$' | head -1)" -resize 640x480 png:-`; it has 10 s, then it
  and everything it started are stopped. A failed capture costs only that turn's picture (the model is told why).
  `--no-vision` turns it off, `--frames-dir <dir>` saves every picture sent, and
  `python3 tools/screen_capture.py shot.png` checks the capture without DeepSeek. The image goes as a `data:` URL
  in the user message ([api-docs.deepseek.com/guides/vision](https://api-docs.deepseek.com/guides/vision/));
  `deepseek-v4-pro` takes no images.
- **What it remembers.** "Done so far" is kept for the whole run (the newest 60 entries): rooms entered, menus
  opened and closed, choices and what was picked, battles, the start of each dialogue, a save the guard refused,
  and notes the model asks to keep by adding `"remember": "..."` to its answer. The last 30 turns come after it.
  Before this, the model saw only six turns, and the program's own dialogue turns at a save point filled them, so a
  model asked to open "the menu" walked back to the save point and opened its menu again and again.

- **One screen at a time.** The steps are played in pieces, a new piece at every press of confirm, cancel or
  menu and every second of a long walk or wait, and the rest is dropped as soon as the screen changes (a dialogue,
  choice, menu or room appears, changes or closes). A held direction is cut only while walking, since in a menu
  pressing it again would move the cursor again. An answer is not played at all if the screen changed (or the save
  menu's cursor moved) while the model was thinking; the model is asked again. In the first run in the game, four confirms in one turn ran
  through the save point's dialogue, its menu and a save; now the model sees each screen.
- **Dialogue without the model.** A box that is still typing is waited for in one turn, until it waits for
  confirm or its text has not grown for 3 s (confirm does not skip typing, and the save point's text has pauses),
  and a box that waits for confirm is confirmed, unless that changed nothing twice; the text still goes into the
  turns the model is shown. `--no-auto-confirm` turns this off.
- **No saving unless asked.** Without `--allow-save`, confirm is refused while the save point menu's cursor is on
  a file slot or on Save in the overwrite question, and the model is told not to try again; the second refusal
  ends the run (exit 1: the goal needs `--allow-save`). This needs the `save_menu` key of the state: with a game
  build that does not have it, nothing stops a save.
- **Ending the run.** The model says when the goal is reached (exit 0) or gives up (1); `--until <key>` or
  `--until <key>=<value>` ends it as soon as the state meets that (exit 0, no call needed); a limit ends it with 3.
  With `--until` the program judges the goal: every turn tells the model whether the check is met yet, and an
  answer "done" before it is met is told back to the model and the run goes on (the third such answer ends it with
  exit 1). Give a goal that is a state an `--until` (for "open the menu": `--until interact=5`).
- **Key.** The environment variable `DEEPSEEK_API_KEY`, or else the file `~/.config/deltarune-online/deepseek.env`
  (`--key-file` picks another) with the line `DEEPSEEK_API_KEY=<key>`; `chmod 600` it, and never put it in the
  repository. The key goes only into the requests to api.deepseek.com and is never printed or logged.
- **Cost.** The default is `deepseek-flash` without thinking (`--model pro`, `--reasoning low|high|max` change
  that). The system prompt is the same in every turn, so DeepSeek's context cache bills it at the cache-hit price
  after the first call. Limits: `--max-steps` calls to the model (default 40), `--max-usd` (default 0.50, counted
  at the peak prices, and the run stops before a call that could pass it), `--max-minutes` (20), and `--pause`
  (0.5 s) between calls. The estimate before a call counts a token for every byte of the prompt, so it is high
  (up to about 0.005 USD a call with the guide and a full history, an image counted as 1024 tokens); the runs in the game cost about 0.0002 USD a
  call at peak prices before the guide and the longer history, and all four together less than 0.01 USD.
- **Log.** One line per turn on the screen (what it saw, why, what it pressed, what changed, what was dropped or
  refused, the money spent so far), and every turn as JSON in `deepseek-player-<date>-<time>.jsonl` (`--log`):
  the state as sent, the model's answer, the frames played, the tokens and their price.
- **Without a key, or without the game.** `--prompt` prints the first turn's prompt and calls no DeepSeek API, and
  `--fake <file>` takes the answers from a file instead of DeepSeek; both avoid needing an API key, but still
  connect to the Agent API of a running game (or `native/test_agent.exe`). Without the game,
  `tests/test_deepseek_player.py` runs the loop against a small game written in Python, shaped like the save point,
  and against a fake API, and `native/test_agent.py solo` runs it against `drnet.dll`.
- **Two players.** Run one player on each PC against its own game, with its own goal. Online every turn waits for
  the lockstep, so both games must be running.

**Tried in the game** (2026-11-23, solo, `deepseek-flash`, a build with `save_menu`), every API call worked:

| Run | Goal | Result |
|---|---|---|
| 1, before the pieces and the guard | walk to the save point and open its menu | 17 calls, 0.0033 USD; 8 turns of zig-zag to the save point, then four confirms in one turn ran through the menu and saved (in a test copy) |
| 2, `--until save_menu` | the same | reached: 4 calls and 10 turns without the model, 0.0008 USD |
| 3 | open its menu, then close it again without saving | reached: 5 calls, 0.0010 USD; the save files unchanged |
| 4, `--max-steps 8` | open the menu and save to your current file | the guard refused confirm on file slot 2, nothing saved; the model then tried again until the step limit (the second refusal now ends the run), 0.0019 USD |

Walking still needs two or three corrections around a target. Waiting for typing boxes in one turn, ending the run
after the second refusal, cutting long walks every second and dropping an answer when the screen changed during the
call came after runs 2 to 4 and are not yet tried in the game. So are the guide, the "done so far" list, the 30
turns, the words for the game's numbers, the goal check and the screenshots, which came after a later run in which
the model, asked to open "the menu", kept going back to the save point. The window capture on Windows has not
run at all yet.

## Protocol

Messages are JSON objects, one per line (`\n`, a `\r` before it is ignored), UTF-8, in both directions. Every
command may carry an `"id"` (a string or a number); the replies to it carry the same id, `null` without one. The
game takes the lines of a connection in the order they came and answers each in that order (`state`, `queued`,
`cleared`, `error`), with two exceptions. `done` comes later, when its input has been played, so it can follow the
answers to commands sent after that input. And a command refused because too many are waiting (see the limits)
gets its error at once, ahead of the answers to the commands that wait. So match replies by `type` and `id`.

| Command | Replies |
|---|---|
| `{"cmd":"state"}` | `{"type":"state","id":…,"state":{…}}` with the state at the start of the next game frame |
| `{"cmd":"input","steps":[{"buttons":["right"],"frames":30},{"buttons":[],"frames":2}]}` | `{"type":"queued","id":…,"frames":32}` in the next frame, and `{"type":"done","id":…,"state":{…}}` when the game has applied the last frame |
| `{"cmd":"clear"}` | `{"type":"cleared","id":…}`: all queued input is dropped; inputs sent before it get no `done` any more. Online, the frames the lockstep has already taken (up to `delay` of them) are still applied |
| anything else | `{"type":"error","id":…,"error":"<what is wrong>"}` |

**Input.** Each step holds its buttons for that many input frames (30 per second); `[]` holds nothing. The
buttons are `down`, `right`, `up`, `left`, `confirm`, `cancel`, `menu`: the action bits 0 to 6 of the local
player, and nothing else (not the mod's teleport or F1 to F3). Several input commands play one after the other
without a gap. The agent's buttons are added to the keyboard's and gamepad's, so a person can step in at any time.

**Limits.** 60 steps in a command, 3600 frames in a step, 10 input commands in the queue, 64 KB in a line. A
command over a limit is answered with an error and nothing of it is queued. A game frame takes up to 32 commands;
more than that at once are refused with `too many commands are waiting for the game`, and that error does not
wait for the frame: it arrives before the replies to the 32. The game keeps at most 4 MB of replies for an agent
that does not read them, then closes the connection.

**When a connection ends,** its queued input ends with it as after a `clear`: the buttons are released, and the
next connection starts with an empty queue.

**`done`.** It is sent at the start of the first frame after the one in which the input's last frame was
applied, so its state shows what the input did. Offline that is the next frame. Online the input travels through
the lockstep like key presses, so it is applied `delay` frames after it was taken, and `done` waits for that.

**Online with `delay=auto`** the lockstep moves the delay with the ping. A key press survives that by being
merged into the next frame or repeated; for a queued input the DLL corrects it (`drnet_agent_bits`): no press is
lost or merged with the next one, and no step gets shorter. A step can last longer, by as many frames as the delay
grew (usually one), when the delay grows during the step's last frames. With a fixed `delay=<frames>` on every
machine every frame is applied exactly once.

**Errors.** A line that is not one JSON object, an unknown `cmd`, steps that are missing or wrong (`frames` must
be a whole number, `30.0` and `3e1` count), an unknown button, an `id` that is neither a string nor a number or
is longer than about 90 bytes. The text says which. Bad input never stops the game.

## The state

Built by the game only when asked (a `state` command, or a `done`), never every frame. A value the running
chapter does not have is `null`.

| Key | Meaning |
|---|---|
| `frame` | frames since the game started (`global.dro_frame`) |
| `chapter`, `room` | `global.chapter`, and the room's name |
| `mode`, `player`, `players`, `online` | the `dronline.ini` mode, the local player (1 to 3), the number of players in the game (the mod's `global.playeramount`; online it starts as `players` of `dronline.ini`), whether the network is on |
| `desync` | the frame of the first desync, -1 for none |
| `interact` | `global.interact`: 0 free to move, 1 dialogue, a cutscene or a save point's menu, 5 the Dark World menu is open, 6 a dialogue started from that menu (an item used, for example), other values as they are |
| `menuno`, `submenu` | `global.menuno`: where the Dark World menu stands, 0 its top level, 1 to 5 one of its pages. It does not say whether the menu is open (`interact` 5 does): it is 0 in every room from the start and -1 only once the menu has been closed there. `global.submenu` as the game has it |
| `battle`, `can_move` | `global.fighting == 1`; `interact == 0` and no battle |
| `players_pos` | a list of `{"player","object","name","x","y","facing"}`. Player 1 is `obj_mainchara`; in the Local Multiplayer mod players 2 and 3 are the `obj_caterpillarchara` with `controls` 1 and 2 (`name`: `susie`, `ralsei`, `noelle`). `facing`: 0 down, 1 right, 2 up, 3 left. A player without an instance is left out |
| `dialogue` | a list, one entry per `obj_writer` (at most 8): `raw` (the text with the game's control codes), `text` (without them), `shown` (characters typed so far), `length`, `waiting` (the box waits for confirm), `x`, `y` |
| `choice` | `null`, or while an `obj_choicer_neo` exists: `options` (texts, at most 8), `selected` (-1: none), `can_choose`. A direction picks an option (0 left, 1 right, 2 up, 3 down), then confirm |
| `save_menu` | `null`, or while a save point's menu (`obj_savemenu`) is open, the game's own numbers for it. `stage`: 0 the main box, 1 the list of file slots, 2 after saving to a slot. `x`, `y`: the cursor in the main box, `x` 0 left or 1 right, `y` 0 top row or 1 bottom row: Save (0,0), Return (1,0), Storage (0,1), Recruits (1,1); `x` 2 means the "saved" message waits for confirm. `slot`: the cursor in the list, 0 to 2 a file, 3 Return; it starts on the file the game was loaded from. `overwrite`: 1 while the question whether to overwrite a file is shown (0 none, 0.5 opening), `overwrite_x` its cursor, 0 Save or 1 Return |
| `camera` | `x`, `y`, `w`, `h` of the view |
| `nearby` | up to 30 instances with a sprite within 240 px of player 1, nearest first: `object`, `x`, `y`, `solid`, `visible`. The players themselves are left out |

`text` is `raw` cleaned the way the game's writer reads it: `&` is a line break, `` ` `` keeps the next character,
`/`, `%` and `|` are dropped, `^` goes with the one character after it and `\` with the two after it (`\E2`,
`\cR`, `\M1`, ...).

`interact`, `menuno`, `submenu` and the numbers of `save_menu` are the game's own, handed over as they are. What
they mean is written here for chapter 5 with the mod; another chapter may use others. A save point's menu draws
its labels itself, so the state has its cursors but not its texts, and the question "return to the title?" that
the menu asks in the Light World of earlier chapters (`stage` 10 and 11) has its cursor outside the state.

The numbers of one stage of `save_menu` keep their last values when the menu is in another: in `stage` 0, `slot`
and `overwrite_x` still show where those cursors stood before. So read `x` and `y` in `stage` 0, `slot` in
`stage` 1, and `overwrite_x` while `overwrite` is 1.

## Security

- The port is bound to 127.0.0.1 and never to the network. There is no password: **any program running on this
  PC can connect** and press the local player's seven buttons, so leave `agent=` off when no agent is used.
- A web page cannot use the port through the browser: a connection whose first line is not a JSON object (an HTTP
  request starts with `POST / HTTP/1.1`) gets one error and is closed before anything else is read.
- The agent can press only what the local player can. Online its input goes through the lockstep like key
  presses, so the other players' games stay in sync and desync detection is unchanged.
- The listener is not inherited by the process a chapter change starts, and the game closes it before the change,
  so the new process can listen on the same port.

## How it is built

`native/drnet.c` ("Agent API") holds the protocol: a thread accepts the connection, reads lines and parses them
with a small JSON reader; the input queue, the limits and the replies live there too. That part needs no game, so
it is tested in CI. `gml/scr_dronline.gml` only calls `drnet_agent_frame` at the start of a frame and hands over
the state as JSON when asked (`drnet_agent_state`), takes the agent's buttons for every input frame
(`drnet_agent_bits`) where scripted input comes in, before `script_record` writes the frame, and closes the port
before a chapter change (`drnet_agent_stop`).

The agent runs on one machine only. Anything its code changed in the game there, the other machines would not do,
and that is a desync. So the state code only reads: no random calls, no instances, data structures, buffers or
surfaces created (their ids are plain numbers and would shift), no game variable written, no events run. More in
[DESIGN.md](DESIGN.md#agent-api).

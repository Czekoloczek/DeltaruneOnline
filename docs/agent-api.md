# Agent API

An AI agent (for example Claude Code on the player's PC) can play DELTARUNE from data instead of screenshots: it
reads the game state as JSON and sends input such as "hold right for 30 frames, then press confirm". Version 1
covers the **overworld and dialogue**. In a battle the agent can only see `"battle": true`.

> **Status: not yet tested in the game.** The network side (protocol, input queue, timing) is tested without the
> game, on Linux under Wine and on Windows (`native/test_agent.py`). The GML that reads the game state has never
> been compiled by UndertaleModTool or run in the game, and the names of the game's objects and variables below
> come from the first draft's specification: nobody has checked them in the game yet.

## Turning it on

`agent=<port>` in `[online]` of `dronline.ini`, in `mode=solo`, `host` or `client` (not `record`/`replay`). No
key, or `agent=0`: nothing is opened, and the game behaves as before.

`drnet.dll` then listens for TCP connections on **127.0.0.1:\<port\>**, this computer only. The agent is a program
on the same PC; under Proton it can be a Linux program, because Wine's sockets are the host's. One agent at a time:
while one is connected, another connection is answered with `another agent is connected` and closed. After a
chapter change (a new game process) the agent connects again.

```
python3 tools/agent_client.py state
python3 tools/agent_client.py do "20 right; tap confirm 2 15"
```

`tools/agent_client.py` is a client for the command line and a small module for agents written in Python
(standard library only; `python3 tools/agent_client.py --help`). Its steps use the text of
`tools/input_script.py`. Scripts of any length work: the client cuts them into commands and keeps the game's
queue filled.

## Protocol

Messages are JSON objects, one per line (`\n`, a `\r` before it is ignored), UTF-8, in both directions. Every
command may carry an `"id"` (a string or a number); the replies to it carry the same id, `null` without one. The
game answers the lines of a connection in the order they came.

| Command | Replies |
|---|---|
| `{"cmd":"state"}` | `{"type":"state","id":…,"state":{…}}` with the state at the start of the next game frame |
| `{"cmd":"input","steps":[{"buttons":["right"],"frames":30},{"buttons":[],"frames":2}]}` | `{"type":"queued","id":…,"frames":32}` in the next frame, and `{"type":"done","id":…,"state":{…}}` when the game has applied the last frame |
| `{"cmd":"clear"}` | `{"type":"cleared","id":…}`: all queued input is dropped; inputs sent before it get no `done` any more |
| anything else | `{"type":"error","id":…,"error":"<what is wrong>"}` |

**Input.** Each step holds its buttons for that many input frames (30 per second); `[]` holds nothing. The
buttons are `down`, `right`, `up`, `left`, `confirm`, `cancel`, `menu`: the action bits 0 to 6 of the local
player, and nothing else (not the mod's teleport or F1 to F3). Several input commands play one after the other
without a gap. The agent's buttons are added to the keyboard's and gamepad's, so a person can step in at any time.

**Limits.** 60 steps in a command, 3600 frames in a step, 10 input commands in the queue, 64 KB in a line. A
command over a limit is answered with an error and nothing of it is queued. A game frame takes up to 32 commands;
more than that at once are refused with `too many commands are waiting for the game`. The game keeps at most 4 MB
of replies for an agent that does not read them, then closes the connection.

**When a connection ends,** its queued input ends with it: the buttons are released, and the next connection
starts with an empty queue.

**`done`.** It is sent at the start of the first frame after the one in which the input's last frame was
applied, so its state shows what the input did. Offline that is the next frame. Online the input travels through
the lockstep like key presses, so it is applied `delay` frames after it was taken, and `done` waits for that.

**Online with `delay=auto`** the lockstep moves the delay with the ping. A key press survives that by being
merged into the next frame or repeated; for a queued input the DLL corrects it (`drnet_agent_bits`): no press is
lost or merged with the next one, and no step gets shorter. A step can last a frame or two longer when the delay
grows during its last frames. With a fixed `delay=<frames>` on every machine every frame is applied exactly once.

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
| `mode`, `player`, `players`, `online` | the `dronline.ini` mode, the local player (1 to 3), the number of players, whether the network is on |
| `desync` | the frame of the first desync, -1 for none |
| `interact` | `global.interact`: 0 free to move, 1 dialogue or cutscene, 6 the dark world menu, other values as they are |
| `menuno`, `submenu` | `global.menuno` (-1: no menu), `global.submenu` |
| `battle`, `can_move` | `global.fighting == 1`; `interact == 0` and no battle |
| `players_pos` | a list of `{"player","object","name","x","y","facing"}`. Player 1 is `obj_mainchara`; in the Local Multiplayer mod players 2 and 3 are the `obj_caterpillarchara` with `controls` 1 and 2 (`name`: `susie`, `ralsei`, `noelle`). `facing`: 0 down, 1 right, 2 up, 3 left. A player without an instance is left out |
| `dialogue` | a list, one entry per `obj_writer` (at most 8): `raw` (the text with the game's control codes), `text` (without them), `shown` (characters typed so far), `length`, `waiting` (the box waits for confirm), `x`, `y` |
| `choice` | `null`, or while an `obj_choicer_neo` exists: `options` (texts, at most 8), `selected` (-1: none), `can_choose`. A direction picks an option (0 left, 1 right, 2 up, 3 down), then confirm |
| `camera` | `x`, `y`, `w`, `h` of the view |
| `nearby` | up to 30 instances with a sprite within 240 px of player 1, nearest first: `object`, `x`, `y`, `solid`, `visible`. The players themselves are left out |

`text` is `raw` cleaned the way the game's writer reads it: `&` is a line break, `` ` `` keeps the next character,
`/`, `%` and `|` are dropped, `^` goes with the one character after it and `\` with the two after it (`\E2`,
`\cR`, `\M1`, ...).

## Security

- The port is bound to 127.0.0.1 and never to the network. There is no password: **any program running on this
  PC can connect** and press the local player's seven buttons, so leave `agent=` off when no agent is used.
- A web page cannot use the port through the browser: a connection whose first line is not a JSON object (an HTTP
  request starts with `POST / HTTP/1.1`) gets one error and is closed before anything else is read.
- The agent can press only what the local player can. Online its input goes through the lockstep like key
  presses, so the other players' games stay in sync and desync detection is unchanged.
- The listener is not inherited by the process a chapter change starts, so the new process can listen on the same
  port.

## How it is built

`native/drnet.c` ("Agent API") holds the protocol: a thread accepts the connection, reads lines and parses them
with a small JSON reader; the input queue, the limits and the replies live there too. That part needs no game, so
it is tested in CI. `gml/scr_dronline.gml` only calls `drnet_agent_frame` at the start of a frame and hands over
the state as JSON when asked (`drnet_agent_state`), and takes the agent's buttons for every input frame
(`drnet_agent_bits`) where scripted input comes in, before `script_record` writes the frame.

The agent runs on one machine only. Anything its code changed in the game there, the other machines would not do,
and that is a desync. So the state code only reads: no random calls, no instances, data structures, buffers or
surfaces created (their ids are plain numbers and would shift), no game variable written, no events run. More in
[DESIGN.md](DESIGN.md#agent-api).

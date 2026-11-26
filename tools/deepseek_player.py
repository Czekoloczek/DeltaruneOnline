#!/usr/bin/env python3
"""DeepSeek plays DELTARUNE through the Agent API: reads the game state, asks DeepSeek what to press, presses it.

    python3 tools/deepseek_player.py "walk to the save point and save"

The game must run with agent=<port> in dronline.ini (docs/agent-api.md). Every turn the player reads the state,
sends DeepSeek a summary of it (the game's numbers in words) with the goal, a list of what is done so far and the
last 30 turns, and plays the buttons the model chose (at most --max-frames frames a turn), until the model says the
goal is reached, --until is met, or a limit ends the run. Version 1 of the Agent API covers the overworld and
dialogue; in a battle the model sees only "battle".

What the model is told:
  - the system prompt, the same every turn (so DeepSeek's context cache bills it cheaply from the second call):
    the buttons and steps, how walking works, the rules of the run, and a guide to the game
    (tools/deepseek_guide.md: what each button does, what "the menu" means, the Dark World menu, save points,
    dialogue, rooms and exits, battles; --guide picks another file, --guide none leaves it out);
  - "done so far", kept for the whole run (the newest 60 entries): rooms entered, menus opened and closed, choices,
    battles, the start of each dialogue, refused saves, and notes the model asks to keep ("remember" in its
    answer), so it does not redo what it already did after the turn has scrolled out of the last 30;
  - a screenshot of the game every turn (vision, on by default with deepseek-flash): the game's window scaled to its
    own 640x480, about 206 image tokens, about 0.00006 USD at the peak price. The camera follows the player, so a
    picture is only good for the turn it was taken in; the model can put "look" among its steps to be asked again
    with a new picture partway through a long move (the steps after "look" are not played). On Windows the window
    is captured directly (tools/screen_capture.py: PrintWindow, which never holds other windows; --screen-grab
    copies the screen when PrintWindow gives black, only while nothing overlaps the game); elsewhere
    --screenshot-cmd gives a command that prints the image. When a capture fails, the turn goes without a picture
    and the model is told why;
  - with --until, whether the program's goal check is met yet. The program then judges the goal: an answer
    "done" before the check is met is told back to the model and the run goes on (the third one ends it, exit 1);
  - the room's exits: the doors the state lists anywhere in the room (exits, also invisible ones), with their box,
    the edge they are at and the offset from the player, and where this run came into and left the room (the way
    back is where it came in; a way out without a door object shows up there once it has been used);
  - a warning when the player walked back and forth along one line in its last 8 turns (in the first co-op run
    both players walked left and right for 140 turns past a dark path going down);
  - with --notes, what the other players are doing and the notes they wrote (see "Two players on one PC").
The system prompt tells the model to walk at most 45 frames at a time when it has no target in nearby or exits, so
it does not run past an opening, and how to leave through a door.

A turn's input is played one press at a time (a press of confirm, cancel or menu starts the next piece; a long walk
or wait is cut every second), and the rest is dropped as soon as the screen changes: a dialogue, choice, menu or
room appears, changes or closes. So a model that sends "tap confirm 4 10" sees each new screen instead of running
through it. An answer is not played if the screen changed (or the save menu's cursor moved) while the model
was thinking. A dialogue box that is still typing is waited for (until it waits, or its text has not grown for
3 s), and one that waits for confirm is confirmed, without asking the model (--no-auto-confirm turns both off).

Saving is guarded: unless --allow-save is given, confirm is refused while the save point menu's cursor is on a
file slot or on "Save" in the overwrite question, so the model cannot save over a file; after the second refusal
the run ends with exit code 1. The guard needs the
save_menu key of the state; a game build without it shows no save menu, and then nothing stops a save.

The key comes from the environment variable DEEPSEEK_API_KEY, or else from a file (--key-file, default
~/.config/deltarune-online/deepseek.env) holding the line DEEPSEEK_API_KEY=<key> (a line with only the key works
too). Keep that file outside the repository, readable only by you (chmod 600). The key only goes into the
Authorization header of the requests to api.deepseek.com: it is never printed or logged.

Limits (each ends the run with exit code 3):
  --max-steps <n>     calls to the model, default 40. Turns the player handles itself (waiting for a typing box,
                      confirming one that waits) do not count, but at most 5 times as many of them
  --max-usd <usd>     spending on DeepSeek, default 0.50. Counted from the tokens the API reports, at the peak
                      prices, so the real cost is the same or lower. The run stops before a call that could pass it
                      (a call is reckoned at its worst: the whole prompt uncached, up to about 0.006 USD)
  --max-minutes <m>   wall time, default 20
  --max-frames <n>    input frames of a model's turn, 30 to 3600, default 150 (5 s); a longer answer is cut
                      there, and one of more than 3600 frames is refused (the program's own turns are short:
                      a confirm is 6 frames, waiting for a typing box at most 300)

Other options:
  --until <cond>      end the run (exit 0) as soon as the state meets this, without asking the model: a key that is
                      present and not null or false (--until save_menu: the save point menu is open), or key=value
                      with the value as JSON (--until interact=5, --until save_menu=null; a value that is not JSON
                      is a string: --until room=room_town_school). The model is told about it
  --allow-save        let the model save to a file (the guard above is off)
  --guide <file>      the game guide for the system prompt (default tools/deepseek_guide.md); none: no guide
  --no-vision         send no screenshots (deepseek-v4-pro takes none anyway)
  --screenshot-cmd <c>  a shell command that prints one PNG/JPEG/GIF/WebP image of the game (needed off Windows;
                      see tools/screen_capture.py for an X11/XWayland example)
  --window <title>    part of the game window's title on Windows (default DELTARUNE); --window-pid <pid> picks
                      one of two copies on one PC
  --screen-grab       on Windows, when PrintWindow gives a black picture, copy the game's part of the screen
                      instead; only if no other window overlaps the game just before and just after the copy,
                      since the picture goes to DeepSeek (see tools/screen_capture.py)
  --frames-dir <dir>  also save every screenshot sent, as turn-NNN.png (to see what the model saw)
  --notes <file>      a notes file shared with the other players' programs on this PC (JSON lines; made if it is
                      missing): see "Two players on one PC"
  --port <port>       the agent= port of dronline.ini (default 8765); --host, default 127.0.0.1
  --model <model>     flash (deepseek-flash, default) or pro (deepseek-v4-pro)
  --reasoning <r>     none (default: fast and cheap), low, high, max. Thinking costs many more output tokens, and
                      the spending limit reserves a call's whole output cap (3000, 12000, 32000 tokens), so raise
                      --max-usd with it
  --pause <s>         at least this long between two calls to DeepSeek (default 0.5)
  --log <file>        the log of every turn as JSON lines (default deepseek-player-<date>-<time>.jsonl here)
  --prompt            print the prompt of the first turn and stop, without calling DeepSeek (needs the game)
  --fake <file>       no DeepSeek and no key: the answers come from this file, one per line, either the model's JSON
                      ({"why": "...", "do": "20 right", "done": false}) or only the steps ("20 right"). After the
                      last line the fake model says the goal is reached. For tests (native/test_agent.py)

Steps are written as in tools/agent_client.py ("20 right; tap confirm 2 15"). Every turn prints one line: the
turn, what the model saw in short, why it chose what it did, what was pressed, and the money spent so far.

Exit code: 0 the model says the goal is reached or --until is met, 1 the game or DeepSeek failed or the model
gave up, 2 the command line or the key is wrong, 3 a limit ended the run, 130 Ctrl+C. The queued input is dropped
at every exit.

Two players online: run one player on each PC, each against its own game, with its own goal. Online, every turn
waits for the lockstep, so both games must be running.

Two players on one PC (two copies of the game, as tools/local_test.bat starts them, each with its own agent= port):
give both programs the same --notes file. Every time its model answers, each program appends one line to it (its
player number, turn, time, room and place, the answer's "why" and the "note" the model wrote for the others), and
each turn it shows its model the other players' latest line and their last 4 notes (lines older than an hour are
left out). Use a new file for each session. A player's own place is in players_pos anyway; the notes add what the
others plan and found. Player 2's screenshot is centred on player 1, whom the camera follows, and the model is
told so.

Tested without the game and without a key (tests/test_deepseek_player.py; native/test_agent.py solo, against
drnet.dll). In the game (2026-11-23, solo, deepseek-flash): the first run ran through the save menu and saved,
which the pieces, the save guard, --until and the walking hints came from; with them two runs reached their goal
and a third, asked to save, was stopped by the guard, with nothing saved, for 0.0008 to 0.0019 USD each. The game
guide, the "done so far" list, the 30-turn history, the words for the game's numbers, the goal check and the
screenshots came after a run that kept going back to the save point when asked to open "the menu"; they are not yet
tried in the game, and the window capture on Windows is not yet tried at all. The exits, the back-and-forth warning
and the notes came after the first co-op run (two players on one PC) and are not yet tried in the game either.
"""
import argparse
import base64
import collections
import datetime
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_client  # noqa: E402
import deepseek_review  # noqa: E402
import input_script  # noqa: E402
import screen_capture  # noqa: E402

MODELS = {"flash": "deepseek-flash", "deepseek-flash": "deepseek-flash", "deepseek-v4-flash": "deepseek-flash",
          "pro": "deepseek-v4-pro", "deepseek-v4-pro": "deepseek-v4-pro"}
# USD per 1M tokens: cache hit, cache miss, output. The peak prices of
# https://api-docs.deepseek.com/quick_start/pricing (2026-11); off-peak is half of them.
PRICES = {"deepseek-flash": (0.006, 0.3, 1.2), "deepseek-v4-pro": (0.044, 1.32, 3.96)}
# Output cap per reasoning level; thinking tokens count as output. The answer itself is about 60 tokens.
MAX_TOKENS = {"none": 300, "low": 3000, "high": 12000, "max": 32000}
DEFAULT_KEY_FILE = os.path.join("~", ".config", "deltarune-online", "deepseek.env")
HISTORY = 30            # turns the model is reminded of
MAX_DONE = 60           # entries of the "done so far" list kept for the whole run (the oldest go first)
MAX_REMEMBER = 120      # characters of a note the model asks to keep
MAX_EARLY_DONE = 3      # answers "done" while --until is not met before the run gives up
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GUIDE = os.path.join(HERE, "deepseek_guide.md")
MAX_NEARBY = 12         # objects of the state's nearby list in the prompt
MAX_EXITS = 8           # doors of the state's exits list in the prompt
MAX_SEEN_EXITS = 8      # places per room where this run came in or left
SEEN_NEAR = 64          # px: a place seen within this far of a door's box is put with that door
SWING_TURNS = 8         # the model's turns looked at for walking back and forth
SWING_STEP, SWING_TURNS_MIN, SWING_BAND = 16, 3, 48     # px a move, changes of direction, px the other way
MAX_NOTE = 200          # characters of a note to the other players
NOTES_SHOWN = 4         # notes of each other player in the prompt
NOTES_MAX_AGE = 3600    # s: older lines of the --notes file are left out
NOTES_TAIL = 256 * 1024     # bytes read from the end of the --notes file
MAX_TEXT = 300          # characters of one dialogue box in the prompt
MAX_BAD = 3             # unusable answers in a row before the run gives up
MAX_REFUSED = 2         # confirms refused by the save guard before the run gives up
WAIT_STEP, WAIT_QUIET, WAIT_MAX = 5, 90, 300    # waiting for a typing box: frames a step, without progress, in all
MAX_ANSWER_FRAMES = 3600    # an answer whose steps make more frames than this is refused, not cut
TEMPLATE_TOKENS = 20    # tokens of the chat template around each message (role, separators), at most
IMAGE_TOKENS = 1024     # tokens of one image at most (api-docs.deepseek.com/guides/vision; 640x480 is about 206)
VISION_MODELS = ("deepseek-flash",)     # deepseek-v4-pro does not take images (Models & Pricing, 2026-11)
AUTO_PER_STEP = 5       # turns without the model allowed per --max-steps
PIECE = 30              # frames: a long walk or wait is played in pieces this long, the screen checked after each
CONFIRM, PRESSES, DIRECTIONS = 16, 16 | 32 | 64, 1 | 2 | 4 | 8      # action bits (agent_client.AGENT_BUTTONS)

SYSTEM = """You play DELTARUNE through a program. Each turn you get the game state, a list of what is already done
in this run, and the last turns, and you answer with the buttons to press next.

Buttons: down right up left confirm cancel menu (or d r u l z x c). Steps, separated by ';':
  <frames> <buttons>            hold buttons for that many frames (30 frames = 1 s), e.g. "20 right", "10 up+left"
  wait <frames>                 press nothing
  tap <buttons> [times] [gap]   press for 1 frame, then nothing for <gap> frames, e.g. "tap confirm 2 10"
At most {max_frames} frames a turn. The program plays your steps until the screen changes (a dialogue, menu,
choice or room appears, changes or closes), drops the rest and asks you again. So in a menu or a choice send one
press a turn. To walk to something whose place you know (in nearby or exits), send one hold of about its distance
divided by the walking speed; without such a target, hold a direction for at most 45 frames, then look at the new
state.{vision_rule}

Moving: x grows to the right, y grows downward. Walking speeds up to about 7 px a frame within a few frames (30
frames of right moved 218 px in one test); the speed measured in this run is shown once you have walked. nearby
lists objects with (dx,dy), where they are from you: hold right for about dx/7 frames when dx > 0, left when dx < 0,
down when dy > 0, up when dy < 0. To use an object, stop about 20 px (one tile) from it, facing it, and tap
confirm once.

Exits: "exits" lists the doors of the room, anywhere in it and also when they are invisible: the edge of the room
each is at, its box, and (dx,dy) from you. To leave through one, first line up with its box (its y range for a door
at the left or right edge, its x range at the top or bottom), then walk into it. Not every way out has a door:
some rooms are left by walking off the edge where the floor goes on. The program remembers where you came into and
left each room in this run and shows that with the exits; the way back is where you came in. If the program says
you are walking back and forth, stop doing that and try another direction or an exit.

Dialogue: "typing" means the text is still appearing; confirm does nothing then (cancel shows the rest at once).
The program waits for typing boxes and confirms boxes that wait for it, so you are asked when something needs a
decision. can_move=false: a dialogue, cutscene or menu holds you; cancel closes menus; a choice is picked with a
direction and then confirm. {save_rule}
If your last input changed nothing, try something else.

Memory: "Done so far" is kept for the whole run: what the program saw happen (rooms entered, menus opened and
closed, choices, dialogue) and the notes you asked it to keep. Read it before you act, and do not do again what
it says is done. To keep a note there, add "remember": "<at most 15 words>" to your answer (where something is,
what you tried, what is left of the goal).{notes_rule}{until_rule}

{guide}

Goal: {goal}

Answer with one JSON object and nothing else:
{{"why": "<at most 15 words>", "do": "<steps>", "done": false}}
or with a note to keep: {{"why": "...", "do": "...", "done": false, "remember": "<at most 15 words>"}}{notes_answer}
"done": true only when the goal is reached (then "do" may be empty). If the goal cannot be reached, answer
"done": true and say why in "why", starting with "give up:"."""
VISION_RULE = """
Screenshot: each turn comes with a picture of the game screen as it is now (the game's own 640x480). {camera} The
state lines are exact; the picture shows what they cannot (walls, paths, puzzles, where things are), but dark paths
can be hard to see on it, so check the exits line too. To look again partway through a long move, put the step
"look" among your steps, e.g. "60 right; look; 40 up": the program plays the steps before it, then asks you again
with a new screenshot; the steps after "look" are not played."""
CAMERA_ME = "The camera follows you, so it shows only the part of the room around you."
CAMERA_P1 = ("The camera follows player 1 (Kris), not you (player {player}): you can be off-centre or off the "
             "screen, so go by your x and y in the state.")
NOTES_ANSWER = """
or with a note for the other players: {"why": "...", "do": "...", "done": false, "note": "<at most 25 words>"}"""
NOTES_RULE = """

Other players: the other players of this game are played by programs like this one, and you share notes with them.
Each turn "Other players" shows what each of them is doing now (its last "why") and the notes it wrote. To tell
them something, add "note": "<at most 25 words>" to your answer: your plan, what you found (an exit, a dead end, a
puzzle), or what you want them to do. Work together: do not both search the same place, and when one of you finds
the way on, say where it is."""
SAVE_ALLOWED = "Saving to a file is allowed in this run, but only if the goal asks for it."
SAVE_REFUSED = ("Saving to a file is not allowed in this run: the program refuses confirm on a file slot and on "
                "Save in the overwrite question. Use Return or cancel there.")


class Stop(Exception):
    """Ends the run: `code` is the exit code, `why` is said in the last line."""

    def __init__(self, code, why):
        super().__init__(why)
        self.code, self.why = code, why


# ---------------------------------------------------------------------------------------------- the prompt


def _num(v):
    return int(round(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def me_of(state):
    """The local player's entry of players_pos, or None."""
    for p in state.get("players_pos") or []:
        if isinstance(p, dict) and p.get("player") == state.get("player", 1):
            return p
    return None


FACING = {0: "down", 1: "right", 2: "up", 3: "left"}
# The game's own numbers in words (docs/agent-api.md, "The state"; chapter 5 with the mod).
INTERACT = {0: "free to walk", 1: "a dialogue, a cutscene or a save point's menu holds you",
            5: "the Dark World menu is open", 6: "a dialogue started from the Dark World menu"}
OBJECTS = {"obj_savepoint": "save point"}


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def save_menu_text(sm):
    """A save point's menu (the save_menu key) in words. The numbers of the other stages keep their last values,
    so only the ones of the stage shown are read."""
    stage, x, y = sm.get("stage"), sm.get("x"), sm.get("y")
    if _is_num(sm.get("overwrite")) and sm["overwrite"] >= 1:
        return "the question whether to overwrite the file, cursor on %s" % (
            {0: "Save", 1: "Return"}.get(_num(sm.get("overwrite_x")), "?"))
    if stage == 0:
        if x == 2:
            return "the main box: the message that the game was saved waits for confirm"
        names = {(0, 0): "Save", (1, 0): "Return", (0, 1): "Storage", (1, 1): "Recruits"}
        return "the main box (Save, Return, Storage, Recruits), cursor on %s" % (
            names.get((_num(x), _num(y)), "?"))
    if stage == 1:
        slot = _num(sm.get("slot"))
        return "the list of file slots, cursor on %s" % (
            "Return" if slot == 3 else "file slot %s" % slot)
    if stage == 2:
        return "the game was just saved to a file"
    return "stage %s" % stage


def compass(dx, dy):
    """An offset from the player in words: "left", "down-left", "here" (within 20 px both ways)."""
    ax, ay = abs(dx), abs(dy)
    if ax < 20 and ay < 20:
        return "here"
    h, v = ("right" if dx > 0 else "left"), ("down" if dy > 0 else "up")
    if ay < 20 or ay * 2 < ax:
        return h
    if ax < 20 or ax * 2 < ay:
        return v
    return v + "-" + h


def exit_box(o):
    """(left, top, right, bottom) of an entry of exits; its x, y as a point without a box; None without either."""
    box = tuple(o.get(k) for k in ("left", "top", "right", "bottom"))
    if all(_is_num(v) for v in box):
        return box
    if _is_num(o.get("x")) and _is_num(o.get("y")):
        return o["x"], o["y"], o["x"], o["y"]
    return None


def box_gap(box, x, y):
    """How far the point (x, y) is from the box, 0 inside it."""
    return max(box[0] - x, 0, x - box[2]) + max(box[1] - y, 0, y - box[3])


EDGES = {"left": "at the left edge", "right": "at the right edge", "top": "at the top edge",
         "bottom": "at the bottom edge", "inside": "inside the room"}


def exit_lines(state, mx, my, seen=()):
    """The exits of the state as lines for the prompt, with (dx,dy) from the player at (mx, my) when it is known,
    and what this run saw there: `seen` holds (x, y, words) of this room ("you came in here from room_x"). A place
    within SEEN_NEAR px of a door's box goes with the door nearest to it; the others get a line of their own."""
    doors = []
    for o in (state.get("exits") or [])[:MAX_EXITS]:
        if isinstance(o, dict) and exit_box(o) is not None:
            doors.append((o, exit_box(o), []))
    alone = []
    for x, y, words in seen:
        near = [(box_gap(box, x, y), i) for i, (_, box, _) in enumerate(doors) if box_gap(box, x, y) <= SEEN_NEAR]
        if near:
            doors[min(near)[1]][2].append(words)
        else:
            alone.append((x, y, words))
    know = _is_num(mx) and _is_num(my)

    def where(x, y):
        if not know:
            return "at x=%s y=%s" % (_num(x), _num(y))
        return "(%+d,%+d) %s" % (round(x - mx), round(y - my), compass(x - mx, y - my))
    out = []
    for o, box, words in doors:
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        line = "%s %s, box x=%s..%s y=%s..%s, %s" % (o.get("object"), EDGES.get(o.get("edge"), "in the room"),
                                                    _num(box[0]), _num(box[2]), _num(box[1]), _num(box[3]),
                                                    where(cx, cy))
        if o.get("to"):
            line += ", leads to %s (going by its variables)" % o["to"]
        out.append(line + "".join("; " + w for w in words))
    for x, y, words in alone:
        out.append("no door object: %s %s" % (words, where(x, y)))
    return out


def summarize(state, seen_exits=()):
    """The state as a few short lines for the prompt. Keys the state lacks are left out. `seen_exits`: what this run
    saw of the exits of this room (exit_lines)."""
    s = state or {}
    out = []
    head = []
    for k in ("chapter", "room", "can_move", "desync"):
        if s.get(k) is None or (k == "desync" and s.get(k) == -1):
            continue
        head.append("%s=%s" % (k, json.dumps(s[k]) if isinstance(s[k], bool) else _num(s[k])))
    size = s.get("room_size")
    if isinstance(size, dict) and _is_num(size.get("w")) and _is_num(size.get("h")):
        head.append("room_size=%sx%s" % (_num(size["w"]), _num(size["h"])))
    if head:
        out.append(" ".join(head))
    if s.get("interact") is not None:
        n = _num(s["interact"])
        line = "interact=%s: %s" % (n, INTERACT.get(n, "the game's own number"))
        # menuno and submenu keep old values while the Dark World menu is closed: shown only while it is open
        if n == 5 and s.get("menuno") is not None:
            m = _num(s["menuno"])
            line += "; menuno=%s: %s" % (m, "its top row of icons" if m == 0 else "one of its pages")
            if s.get("submenu") is not None:
                line += ", submenu=%s" % _num(s["submenu"])
        out.append(line)
    if s.get("battle") is True:
        out.append("battle=true: a battle is on; the program cannot show it to you or play it")
    me = me_of(s)
    mx = my = None
    for p in s.get("players_pos") or []:
        if not isinstance(p, dict):
            continue
        who = "you" if p is me else "player %s" % p.get("player")
        name = (" " + str(p["name"])) if p.get("name") else ""
        facing = (" facing " + FACING.get(p["facing"], str(p["facing"]))) if p.get("facing") is not None else ""
        out.append("%s%s at x=%s y=%s%s" % (who, name, _num(p.get("x")), _num(p.get("y")), facing))
        if p is me:
            mx, my = p.get("x"), p.get("y")
    for d in (s.get("dialogue") or [])[:4]:
        if not isinstance(d, dict):
            continue
        text = " ".join(str(d.get("text") or "").split())[:MAX_TEXT]
        flag = "waits for confirm" if d.get("waiting") else "typing"
        out.append("dialogue (%s): %s" % (flag, json.dumps(text, ensure_ascii=False)))
    c = s.get("choice")
    if isinstance(c, dict):
        opts = [" ".join(str(o).split())[:60] for o in (c.get("options") or [])]
        out.append("choice: %s, selected %s (0 left, 1 right, 2 up, 3 down)" % (
            json.dumps(opts, ensure_ascii=False), c.get("selected")))
    sm = s.get("save_menu")
    if isinstance(sm, dict):
        # The key of the Agent API's save point menu (PR 37 of the game side); the numbers are the game's own.
        out.append("save point menu (open): %s %s" % (save_menu_text(sm),
                                                       json.dumps(sm, separators=(",", ":"))[:200]))
    near = []
    for o in (s.get("nearby") or [])[:MAX_NEARBY]:
        if not isinstance(o, dict):
            continue
        x, y = o.get("x"), o.get("y")
        if isinstance(mx, (int, float)) and isinstance(x, (int, float)) and isinstance(y, (int, float)):
            where = "(%+d,%+d)" % (round(x - mx), round(y - my))
        else:
            where = "(%s,%s)" % (_num(x), _num(y))
        known = OBJECTS.get(o.get("object"))
        near.append("%s%s %s%s" % (o.get("object"), " (%s)" % known if known else "", where,
                                   " solid" if o.get("solid") else ""))
    if near:
        out.append("nearby: " + "; ".join(near))
    ways = exit_lines(s, mx, my, seen_exits) if isinstance(s.get("exits"), list) or seen_exits else []
    if ways:
        out.append("exits:" + "".join("\n  " + w for w in ways))
    elif isinstance(s.get("exits"), list):
        out.append("exits: no door objects in this room")
    return "\n".join(out) if out else json.dumps(s)[:1000]


def change(before, after):
    """What a turn changed, in a few words."""
    if not before or not after:
        return "?"
    bits = []
    if before.get("room") != after.get("room"):
        bits.append("room %s -> %s" % (before.get("room"), after.get("room")))
    a, b = me_of(before), me_of(after)
    if a and b and isinstance(a.get("x"), (int, float)) and isinstance(b.get("x"), (int, float)):
        dx, dy = round(b["x"] - a["x"]), round(b["y"] - a["y"])
        if dx or dy:
            bits.append("moved (%+d,%+d)" % (dx, dy))
    for k in ("can_move", "battle", "interact", "menuno"):
        if before.get(k) != after.get(k):
            bits.append("%s %s -> %s" % (k, before.get(k), after.get(k)))
    texts = lambda s: [d.get("text") for d in boxes(s)]  # noqa: E731
    if texts(before) != texts(after):
        bits.append("dialogue changed")
    for k, name in (("choice", "choice"), ("save_menu", "save point menu")):
        if bool(before.get(k)) != bool(after.get(k)):
            bits.append(name + (" appeared" if after.get(k) else " gone"))
    a, b = before.get("save_menu"), after.get("save_menu")
    if isinstance(a, dict) and isinstance(b, dict) and a != b:
        bits.append("save point menu %s" % json.dumps(b, separators=(",", ":"))[:120])
    return ", ".join(bits) or "nothing changed"


def learn_exits(seen, states):
    """Notes in `seen` (room -> [(x, y, words)]) where this run left a room and came into the next: for every room
    change between two states in a row, where the player was last seen in the old room ("you left here to <new>")
    and first seen in the new one ("you came in here from <old>"; the way back is there). The newest
    MAX_SEEN_EXITS of a room are kept, one per text."""
    prev = None
    for st in states:
        if not isinstance(st, dict):
            continue
        if prev is not None and prev.get("room") and st.get("room") and prev.get("room") != st.get("room"):
            for room, at, words in ((prev["room"], me_of(prev), "you left here to %s" % st["room"]),
                                    (st["room"], me_of(st), "you came in here from %s" % prev["room"])):
                if at and _is_num(at.get("x")) and _is_num(at.get("y")):
                    places = [e for e in seen.get(room, []) if e[2] != words] + [(at["x"], at["y"], words)]
                    seen[room] = places[-MAX_SEEN_EXITS:]
        prev = st
    return seen


def back_and_forth(track):
    """A warning when the player's last SWING_TURNS positions (one per call to the model, all in this room) went
    back and forth along one line: SWING_TURNS_MIN or more changes of direction along x (or y), counting moves of
    SWING_STEP px or more, while the other way stayed within SWING_BAND px. None otherwise."""
    pts = track[-SWING_TURNS:]
    if len(pts) < SWING_TURNS:
        return None
    for axis, name, other, instead in ((0, "x", "y", "up or down"), (1, "y", "x", "left or right")):
        moves = [b[axis] - a[axis] for a, b in zip(pts, pts[1:]) if abs(b[axis] - a[axis]) >= SWING_STEP]
        swings = sum(1 for m, n in zip(moves, moves[1:]) if (m > 0) != (n > 0))
        side = [q[1 - axis] for q in pts]
        if swings >= SWING_TURNS_MIN and max(side) - min(side) < SWING_BAND:
            line = [q[axis] for q in pts]
            return ("in your last %d turns you walked back and forth between %s=%d and %s=%d (%s about %d) and did "
                    "not find the way on. Going further along that line will not help: try %s, an exit from the "
                    "exits lines, or a path on the screenshot that you have not tried." % (
                        len(pts), name, round(min(line)), name, round(max(line)), other,
                        round(sum(side) / len(side)), instead))
    return None


class Notes:
    """The notes file of --notes, shared by the players of one game on one PC. Every time the model answers, each
    player appends one JSON line: its player number, the turn, the time, its room and place, what it is doing (the
    answer's "why") and the note it wrote for the others ("" for none). Each turn it reads the end of the file and
    shows the model what the other players are doing and their latest notes. A line is written with one write call;
    a line that two players wrote at the same moment and that came out broken is skipped."""

    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock

    def write(self, player, turn, state, doing, note):
        """Appends this player's line. Returns "" or what went wrong."""
        me = me_of(state or {}) or {}
        rec = {"player": player, "turn": turn, "time": round(self.clock(), 3), "room": (state or {}).get("room"),
               "x": _num(me.get("x")), "y": _num(me.get("y")), "doing": doing, "note": note}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            return "cannot write the notes file: %s" % e
        return ""

    def read(self):
        """The lines at the end of the file (the last NOTES_TAIL bytes) that are whole JSON objects."""
        try:
            with open(self.path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - NOTES_TAIL))
                data = f.read()
        except OSError:
            return []
        lines = data.decode("utf-8", "replace").splitlines()
        if size > NOTES_TAIL:
            lines = lines[1:]       # cut at the start
        out = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and _is_num(rec.get("player")) and _is_num(rec.get("time")):
                out.append(rec)
        return out

    def lines(self, me):
        """The prompt's lines about the other players than `me`: for each, its last line (when, where, what it is
        doing) and its latest NOTES_SHOWN notes. Lines older than NOTES_MAX_AGE are left out."""
        now = self.clock()
        recs = [r for r in self.read() if _num(r["player"]) != me and now - r["time"] <= NOTES_MAX_AGE]
        clean = lambda v, n: " ".join(str(v or "").split())[:n]   # noqa: E731
        out = []
        for pl in sorted({_num(r["player"]) for r in recs}):
            theirs = [r for r in recs if _num(r["player"]) == pl]
            last = theirs[-1]
            out.append("player %s, %s ago (its turn %s), in %s at x=%s y=%s, doing: %s" % (
                pl, ago(now - last["time"]), _num(last.get("turn")), last.get("room"), _num(last.get("x")),
                _num(last.get("y")), json.dumps(clean(last.get("doing"), 200), ensure_ascii=False)))
            for r in [r for r in theirs if clean(r.get("note"), MAX_NOTE)][-NOTES_SHOWN:]:
                out.append("  player %s wrote %s ago, in %s: %s" % (pl, ago(now - r["time"]), r.get("room"),
                                                                     json.dumps(clean(r.get("note"), MAX_NOTE),
                                                                                ensure_ascii=False)))
        return out


def ago(seconds):
    seconds = max(0, seconds)
    return "%d s" % seconds if seconds < 90 else "%d min" % round(seconds / 60)


def events(before, after, between=()):
    """What happened between two states that is worth keeping for the whole run ("done so far"): rooms, menus,
    choices, battles, the start of a dialogue. Positions are left out: they change every turn. `between` holds the
    states seen in between (after each piece of a turn): a choice's last selection is read there."""
    if not before or not after:
        return []
    out = []
    if before.get("room") != after.get("room") and after.get("room") is not None:
        out.append("entered room %s (from %s)" % (after.get("room"), before.get("room")))
    a, b = _num(before.get("interact")), _num(after.get("interact"))
    if a != 5 and b == 5:
        out.append("opened the Dark World menu")
    elif a == 5 and b != 5 and b != 6:
        out.append("closed the Dark World menu")
    if b == 5 and _num(before.get("menuno")) != _num(after.get("menuno")) and _num(after.get("menuno")) is not None:
        m = _num(after.get("menuno"))
        out.append("Dark World menu: %s" % ("back on its top row" if m == 0 else "opened its page menuno=%s" % m))
    sa, sb = before.get("save_menu"), after.get("save_menu")
    if not isinstance(sa, dict) and isinstance(sb, dict):
        out.append("opened the save point's menu")
    elif isinstance(sa, dict) and not isinstance(sb, dict):
        out.append("closed the save point's menu")
    if isinstance(sb, dict) and (not isinstance(sa, dict) or sa.get("stage") != sb.get("stage")):
        if sb.get("stage") == 1:
            out.append("save point's menu: chose Save, the file slots are shown")
        elif sb.get("stage") == 2:
            out.append("save point's menu: saved to file slot %s" % _num(sb.get("slot")))
    ca, cb = before.get("choice"), after.get("choice")
    if isinstance(cb, dict) and (not isinstance(ca, dict) or ca.get("options") != cb.get("options")):
        out.append("a choice appeared: %s" % json.dumps([" ".join(str(o).split())[:40] for o in
                                                          (cb.get("options") or [])], ensure_ascii=False))
    elif isinstance(ca, dict) and not isinstance(cb, dict):
        last = next((st.get("choice") for st in reversed(list(between))
                     if isinstance(st, dict) and isinstance(st.get("choice"), dict)), ca)
        sel = last.get("selected")
        opts = last.get("options") or []
        picked = opts[sel] if isinstance(sel, int) and 0 <= sel < len(opts) else sel
        out.append("the choice closed (last selected: %s)" % json.dumps(picked, ensure_ascii=False))
    if before.get("battle") is not True and after.get("battle") is True:
        out.append("a battle started")
    elif before.get("battle") is True and after.get("battle") is False:
        out.append("the battle ended")
    first = lambda st: " ".join(str((boxes(st) or [{}])[0].get("text") or "").split())  # noqa: E731
    if boxes(after) and (not boxes(before) or first(before)[:20] != first(after)[:20]):
        out.append("dialogue: %s" % json.dumps(first(after)[:80], ensure_ascii=False))
    return out


def boxes(state):
    return [d for d in (state or {}).get("dialogue") or [] if isinstance(d, dict)]


def screen(state):
    """What the player sees, without positions and cursors: when this changes, the rest of a turn is dropped."""
    s = state or {}
    c, sm = s.get("choice"), s.get("save_menu")
    return (s.get("room"), s.get("interact"), s.get("menuno"), s.get("battle"),
            tuple((d.get("text"), bool(d.get("waiting"))) for d in boxes(s)),
            tuple(str(o) for o in c.get("options") or []) if isinstance(c, dict) else None,
            (sm.get("stage"), sm.get("overwrite"), sm.get("x") == 2) if isinstance(sm, dict) else None)


def save_risk(state):
    """Why a confirm now would save to a file, or None."""
    sm = (state or {}).get("save_menu")
    if not isinstance(sm, dict):
        return None
    if isinstance(sm.get("overwrite"), (int, float)) and sm["overwrite"] > 0 and sm.get("overwrite_x") == 0:
        return "confirm here answers Save to the overwrite question"
    if sm.get("stage") == 1 and sm.get("slot") in (0, 1, 2):
        return "confirm here saves to file slot %d" % sm["slot"]
    return None


def pieces(frames):
    """A turn's frames cut before every new press of confirm, cancel or menu."""
    out, prev = [], 0
    for bits in frames:
        if not out or bits & PRESSES & ~prev:
            out.append([])
        out[-1].append(bits)
        prev = bits
    return out


PRESENT = object()      # the value of an --until without '=': the key is there and not null or false


def until_text(until):
    return until[0] if until[1] is PRESENT else "%s=%s" % (until[0], json.dumps(until[1]))


def parse_until(text):
    """--until "key" or "key=value" -> (key, PRESENT or the value; "null" is None)."""
    key, eq, value = (text or "").partition("=")
    key = key.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise ValueError("--until takes a key of the state or key=value, not %r" % text)
    if not eq:
        return key, PRESENT
    try:
        return key, json.loads(value)
    except ValueError:
        return key, value.strip()


def met(state, until):
    if not until:
        return False
    key, value = until
    have = (state or {}).get(key)
    if value is PRESENT:
        return have is not None and have is not False
    return have == value or (isinstance(value, str) and str(have) == value)


_guides = {}


def read_guide(path=None):
    """The game guide that goes into the system prompt (tools/deepseek_guide.md, or --guide); "" for none."""
    path = DEFAULT_GUIDE if path is None else path
    if not path:
        return ""
    if path not in _guides:
        with open(path, encoding="utf-8") as f:
            _guides[path] = f.read().strip()
    return _guides[path]


def messages(goal, max_frames, state, history, turn, max_steps, allow_save=False, until=None, speed=None,
             done=None, guide=None, vision=False, shot=None, no_shot="", player=1, others=None, swing=None,
             seen_exits=()):
    """The system prompt (with the game guide) stays the same for the whole run, so DeepSeek's context cache bills
    it at the cache-hit price from the second call on; only the user message changes: the list of what is done so
    far, what the other players are doing (`others`: lines from Notes.lines, None without --notes), the last
    HISTORY turns, a warning about walking back and forth (`swing`), and the state with what this run saw of the
    room's exits (`seen_exits`). `player` is the local player, the same for the whole run."""
    recent = "\n".join(history[-HISTORY:]) or "(first turn)"
    user = "Turn %d of %d.\n" % (turn, max_steps)
    user += "Done so far in this run (kept for the whole run):\n%s\n" % ("\n".join(done or []) or "(nothing yet)")
    if others is not None:
        user += "Other players (from the notes you share):\n%s\n" % ("\n".join(others) or "(nothing from them yet)")
    if until:
        user += "Goal check by the program (the state has %s): %s.\n" % (
            until_text(until), "met" if met(state, until) else "not met yet")
    user += "Last turns:\n%s\n" % recent
    if speed:
        user += "Walking speed measured in this run: about %.1f px a frame.\n" % speed
    if swing:
        user += "Warning from the program: %s\n" % swing
    user += "State now:\n%s" % summarize(state, seen_exits)
    if vision and shot is None:
        user += "\n(no screenshot this turn: %s)" % (no_shot or "the capture failed")
    until_rule = ""
    if until:
        until_rule = ("\nThe program checks the goal itself: it ends the run as soon as the state has %s, and each "
                      "turn says whether that is met yet. Until then the goal is not reached, so do not answer "
                      "\"done\": true (except to give up)." % until_text(until))
    text = read_guide(guide)
    camera = CAMERA_ME if not _is_num(player) or player <= 1 else CAMERA_P1.format(player=_num(player))
    system = SYSTEM.format(goal=goal, max_frames=max_frames, until_rule=until_rule,
                           guide=("About the game:\n\n" + text) if text else "",
                           vision_rule=VISION_RULE.format(camera=camera) if vision else "",
                           save_rule=SAVE_ALLOWED if allow_save else SAVE_REFUSED,
                           notes_rule=NOTES_RULE if others is not None else "",
                           notes_answer=NOTES_ANSWER if others is not None else "")
    content = user
    if vision and shot is not None:
        # https://api-docs.deepseek.com/guides/vision: an OpenAI-style list of blocks, the image as a data: URL
        content = [{"type": "text", "text": user},
                   {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (
                       shot.mime, base64.b64encode(shot.data).decode("ascii"))}}]
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]


def text_of(content):
    """The text of a message's content: the string itself, or the text blocks of a list."""
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def prompt_tokens_max(msgs):
    """At most how many input tokens a request is: a token for every byte of text (a token of the byte-level
    tokenizer holds at least one), the chat template, and IMAGE_TOKENS for every image."""
    total = 0
    for m in msgs:
        c = m["content"]
        images = 0 if isinstance(c, str) else sum(1 for b in c if isinstance(b, dict) and b.get("type") == "image_url")
        total += len(text_of(c).encode("utf-8")) + TEMPLATE_TOKENS + images * IMAGE_TOKENS
    return total


# ---------------------------------------------------------------------------------------------- the answer


def frames_in(text):
    """How many frames steps written as text make, counted without building them (a model could answer
    "999999999 right"). Steps that do not parse count 0: text_to_frames says what is wrong with them. A number of
    seconds too big for a frame count ("wait 1e308s") raises ValueError."""
    total = 0
    for line in text.splitlines():
        for step in line.split("#", 1)[0].split(";"):
            w = step.split()
            try:
                if not w:
                    continue
                if w[0].lower() == "wait" and len(w) == 2:
                    total += input_script.parse_frames(w[1])
                elif w[0].lower() == "tap" and 2 <= len(w) <= 4:
                    times = input_script.parse_frames(w[2], "times") if len(w) > 2 else 1
                    gap = input_script.parse_frames(w[3], "gap", allow_zero=True) if len(w) > 3 else 1
                    total += times * (1 + gap)
                elif len(w) == 2:
                    total += input_script.parse_frames(w[0])
            except OverflowError:
                raise ValueError("the step %r makes too many frames" % step.strip()[:60])
            except ValueError:
                pass
    return total


Answer = collections.namedtuple("Answer", "why do frames done asked remember look note")


def split_look(do):
    """Steps text -> (the steps before the first "look" step, True), or (the text, False) without one. A line's
    "#" starts a comment, as in the Agent API (tools/agent_client.py), so a "look" after it is not a step."""
    before = []
    for line in do.splitlines():
        steps = line.split("#", 1)[0].split(";")
        for i, step in enumerate(steps):
            if step.strip().lower() == "look":
                before.append(";".join(steps[:i]))
                return "\n".join(before).strip().rstrip(";").strip(), True
        before.append(line)
    return do, False


def parse_answer(content, max_frames):
    """The model's text -> Answer(why, steps text as given, per-frame bits cut to max_frames, done, frames asked for,
    the note it asks to keep or "", whether the steps stop at a "look", the note for the other players or "").
    Raises ValueError with what is wrong."""
    text = (content or "").strip()
    try:
        obj = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)        # a JSON object inside a code fence or other words
        if not m:
            raise ValueError("not JSON: %r" % text[:200])
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            raise ValueError("not JSON: %r" % text[:200])
    if not isinstance(obj, dict):
        raise ValueError("not a JSON object: %r" % text[:200])
    why = " ".join(str(obj.get("why") or "").split())[:200]
    do = obj.get("do") or ""
    if not isinstance(do, str):
        raise ValueError('"do" must be a string of steps, not %r' % (do,))
    done = obj.get("done") is True
    play_do, look = split_look(do)
    asked = frames_in(play_do)
    if asked > max(MAX_ANSWER_FRAMES, max_frames):
        raise ValueError("the steps make %d frames; send at most %d a turn" % (asked, max_frames))
    try:
        frames = agent_client.text_to_frames(play_do)   # ScriptError is a ValueError
    except OverflowError:
        raise ValueError("a step makes too many frames")
    agent_client.frames_to_steps(frames)            # only the seven buttons of the agent
    keep = obj.get("remember")
    keep = " ".join(keep.split())[:MAX_REMEMBER] if isinstance(keep, str) else ""
    tell = obj.get("note")
    tell = " ".join(tell.split())[:MAX_NOTE] if isinstance(tell, str) else ""
    return Answer(why, do.strip(), frames[:max_frames], done, len(frames), keep, look, tell)


# ---------------------------------------------------------------------------------------------- models


def usage_cost(usage, prices):
    """USD for one call, from the token counts the API returned."""
    u = usage or {}
    hit = u.get("prompt_cache_hit_tokens", 0) or 0
    miss = u.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = max((u.get("prompt_tokens", 0) or 0) - hit, 0)
    out = u.get("completion_tokens", 0) or 0
    return (hit * prices[0] + miss * prices[1] + out * prices[2]) / 1e6


class DeepSeek:
    """DeepSeek's chat completions API (the request and the retries of tools/deepseek_review.py)."""

    def __init__(self, key, model, reasoning, http=deepseek_review.http_json, sleep=time.sleep):
        self.key, self.model, self.reasoning, self.http, self.sleep = key, model, reasoning, http, sleep
        self.max_tokens = MAX_TOKENS[reasoning]

    def ask(self, msgs, seconds=120):
        """(the answer's text, usage), within `seconds` with the retries. Raises RuntimeError with a message
        without the key."""
        body = deepseek_review.request_body(self.model, self.reasoning, msgs, MAX_TOKENS)
        body["response_format"] = {"type": "json_object"}
        try:
            answer = deepseek_review.call_deepseek(self.key, body, self.http, self.sleep, attempts=2, timeout=60,
                                                   budget=min(seconds, 120))
        except RuntimeError as e:
            raise RuntimeError(deepseek_review.redact(str(e), self.key))
        choices = answer.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(
            choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise RuntimeError("DeepSeek answered without a message")
        usage = answer.get("usage")
        return content, usage if isinstance(usage, dict) else {}


class FakeModel:
    """Answers from a list, for tests: no network, no key. Its token counts are made up from the prompt's length,
    so that the spending limit can be tested too."""

    max_tokens = 100

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    @classmethod
    def from_file(cls, path):
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line if line.startswith("{") else json.dumps({"why": "fake", "do": line}))
        return cls(out)

    def ask(self, msgs, seconds=120):
        self.asked.append(msgs)
        text = self.answers.pop(0) if self.answers else '{"why": "fake answers ran out", "do": "", "done": true}'
        chars = sum(len(text_of(m["content"])) for m in msgs)
        return text, {"prompt_tokens": chars // 4, "prompt_cache_hit_tokens": 0, "completion_tokens": len(text) // 4}


def key_from_text(text):
    """The key in the text of a key file: the value of a DEEPSEEK_API_KEY=... line (with or without 'export' and
    quotes), or else the first line that is not empty or a # comment."""
    bare = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if m:
            if m.group(1) == "DEEPSEEK_API_KEY":
                return m.group(2).strip().strip("'\"").strip()
        elif not bare:
            bare = line
    return bare


def load_key(env, key_file):
    """The API key: DEEPSEEK_API_KEY, or else the key file. Raises Stop(2) without one."""
    key = (env.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key
    path = os.path.expanduser(key_file)
    try:
        with open(path, encoding="utf-8") as f:
            key = key_from_text(f.read(4096))
    except OSError:
        key = ""
    if not key:
        raise Stop(2, "no DeepSeek key: set DEEPSEEK_API_KEY, or put the line DEEPSEEK_API_KEY=<key> in %s" % path)
    return key


# ---------------------------------------------------------------------------------------------- the run


class Log:
    """One JSON line per turn in the log file, and one short line per turn on the screen."""

    def __init__(self, path, out=sys.stdout):
        self.path, self.out = path, out
        self.f = open(path, "a", encoding="utf-8") if path else None

    def write(self, record, line=None):
        if self.f:
            self.f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.f.flush()
        if line is not None:
            print(line, file=self.out)
            self.out.flush()

    def close(self):
        if self.f:
            self.f.close()


def short(state):
    """The state in a few words for the screen."""
    me = me_of(state or {}) or {}
    bits = [str(state.get("room") or "?")]
    if isinstance(me.get("x"), (int, float)):
        bits.append("(%d,%d)" % (round(me["x"]), round(me["y"])))
    if state.get("can_move") is False:
        bits.append("busy")
    if state.get("battle"):
        bits.append("battle")
    if any(isinstance(d, dict) for d in state.get("dialogue") or []):
        bits.append("dialogue")
    if state.get("choice"):
        bits.append("choice")
    return " ".join(bits)


def auto_action(state):
    """The steps the player plays itself, without the model: waiting for a typing box, confirming one that waits.
    None when something needs a decision (a choice, a menu, a battle, nothing to read)."""
    if state.get("choice") or state.get("save_menu") or state.get("battle") or not boxes(state):
        return None
    return "tap confirm; wait 4" if any(d.get("waiting") for d in boxes(state)) else "wait"


def wait_typing(game, state):
    """Waits in short steps until a box waits for confirm, the screen changes, or the text has not grown for
    WAIT_QUIET frames (a box with pauses in its text shows "typing" for seconds). Returns (state, frames waited)."""
    start, waited, quiet = state, 0, 0
    while waited < WAIT_MAX and quiet < WAIT_QUIET:
        before = state
        state = game.do("wait %d" % WAIT_STEP)
        waited += WAIT_STEP
        if screen(state) != screen(start) or any(d.get("waiting") for d in boxes(state)):
            break
        quiet = quiet + WAIT_STEP if progress(state) == progress(before) else 0
    return state, waited


def progress(state):
    return screen(state), tuple(d.get("shown") for d in boxes(state))


def walked(frames, before, after):
    """(frames a direction was held, px moved) of a turn that stayed in its room, or None."""
    a, b = me_of(before), me_of(after)
    held = sum(1 for bits in frames if bits & DIRECTIONS)
    if not a or not b or before.get("room") != after.get("room") or held < 8:
        return None
    try:
        dist = max(abs(b["x"] - a["x"]), abs(b["y"] - a["y"]))
    except (KeyError, TypeError):
        return None
    return (held, dist) if dist > 0 else None


def walking(state):
    """True when the player walks freely: a held direction moves Kris, it does not move a menu's cursor."""
    s = state or {}
    return bool(s.get("can_move")) and not boxes(s) and not s.get("choice") and not s.get("save_menu") \
        and not s.get("battle")


def play_turn(game, frames, state, allow_save, until, seen=None):
    """Plays a turn's frames piece by piece. Returns (state after, frames played, why the rest was dropped or "").
    The state after each piece is appended to `seen` when it is given."""
    played = 0
    parts = pieces(frames)
    i = 0
    while i < len(parts):
        part = parts[i]
        # A long walk or wait goes in pieces of PIECE frames, so a door or a cutscene on the way ends it. Only
        # without a press in it (letting go of a held button between pieces would press it again), and directions
        # only while walking (in a menu a held direction pressed again would move the cursor again).
        if len(part) > PIECE and not any(b & PRESSES for b in part) and (walking(state) or not any(part)):
            parts[i:i + 1] = [part[:PIECE], part[PIECE:]]
            part = parts[i]
        risk = save_risk(state)
        if risk and part[0] & CONFIRM and not allow_save:
            return state, played, ("refused: %s, and saving is not allowed in this run. Do not try again: if the "
                                   "goal needs saving, give up" % risk)
        before = state
        state = game.send_steps(agent_client.frames_to_steps(part))
        if seen is not None:
            seen.append(state)
        played += len(part)
        if i + 1 < len(parts) and (screen(state) != screen(before) or met(state, until)):
            return state, played, "the rest (%d frames) dropped: the screen changed" % (len(frames) - played)
        i += 1
    return state, played, ""


def play(game, model, goal, *, max_steps=40, max_usd=0.5, max_minutes=20.0, max_frames=150, pause=0.5,
         prices=PRICES["deepseek-flash"], auto_confirm=True, allow_save=False, until=None, log=None, tally=None,
         guide=None, capture=None, frames_dir=None, notes=None, clock=time.monotonic, sleep=time.sleep):
    """The loop. Returns (0, why it ended) when the model says the goal is reached or `until` is met, raises Stop
    otherwise. tally["usd"] holds the USD spent so far, also after a Stop. With `until`, the model's own "done" does
    not end the run: it is told that the program's check is not met yet. `notes`: the Notes shared with the other
    players (--notes), or None."""
    log = log or Log(None)
    tally = {} if tally is None else tally
    tally["usd"] = spent = 0.0
    start = clock()
    last_call = None
    history = []
    bad = calls = autos = auto_stuck = refused = early = 0
    done_list = []              # "done so far": kept for the whole run, the oldest dropped past MAX_DONE

    def keep(turn, items):
        for item in items:
            done_list.append("turn %d: %s" % (turn, item))
        del done_list[:-MAX_DONE]
    walk = None                 # (frames a direction was held, px) of the longest walk so far
    seen_exits = {}             # room -> [(x, y, words)]: where this run came into and left each room
    track, track_room = [], None    # the player's place at each call to the model, in this room
    state = game.state()
    player = _num(state.get("player")) if _is_num(state.get("player")) else 1
    log.write({"event": "start", "goal": goal, "state": summarize(state)},
              "goal: %s\nstart: %s" % (goal, short(state)))
    turn = 0
    while True:
        if met(state, until):
            return 0, "--until %s is met" % until_text(until)
        if clock() - start > max_minutes * 60:
            raise Stop(3, "time limit (%g min)" % max_minutes)
        turn += 1
        do = auto_action(state) if auto_confirm and auto_stuck < 2 else None
        if do:
            autos += 1
            if autos > AUTO_PER_STEP * max_steps:
                raise Stop(3, "%d turns without the model" % (autos - 1))
            before = state
            if do == "wait":
                state, waited = wait_typing(game, state)
                do = "wait %d" % waited
            else:
                state = game.do(do)
            what = change(before, state)
            keep(turn, events(before, state))
            learn_exits(seen_exits, [before, state])
            auto_stuck = auto_stuck + 1 if progress(before) == progress(state) else 0
            said = " / ".join(" ".join(str(d.get("text") or "").split()) for d in boxes(before))
            if do.startswith("tap"):
                history.append("turn %d: dialogue %s, the program tapped confirm -> %s" % (
                    turn, json.dumps(said[:160], ensure_ascii=False), what))
            log.write({"event": "turn", "turn": turn, "auto": True, "dialogue": said, "do": do, "result": what},
                      "%3d  %-34s  (auto) %s | %s -> %s" % (
                          turn, short(before)[:34], json.dumps(said[:50], ensure_ascii=False), do, what))
            continue
        if calls >= max_steps:
            raise Stop(3, "step limit (%d)" % max_steps)
        calls += 1
        auto_stuck = 0
        speed = walk[1] / walk[0] if walk else None
        shot, no_shot = None, ""
        if capture is not None:
            try:
                shot = capture()
            except screen_capture.CaptureError as e:
                no_shot = str(e)
        shot_file = None
        if shot is not None and frames_dir:
            shot_file = os.path.join(frames_dir, "turn-%03d.%s" % (turn, shot.mime.split("/")[1]))
            try:
                with open(shot_file, "wb") as f:
                    f.write(shot.data)
            except OSError:
                shot_file = None
        if state.get("room") != track_room:
            track, track_room = [], state.get("room")
        me = me_of(state)
        if me and _is_num(me.get("x")) and _is_num(me.get("y")):
            track.append((me["x"], me["y"]))
        swing = back_and_forth(track)
        others = notes.lines(player) if notes is not None else None
        msgs = messages(goal, max_frames, state, history, calls, max_steps, allow_save, until, speed, done_list,
                        guide, vision=capture is not None, shot=shot, no_shot=no_shot, player=player, others=others,
                        swing=swing, seen_exits=seen_exits.get(state.get("room"), ()))
        # The next call costs at most: the whole prompt at the cache-miss price, a token for every byte (a token
        # of the byte-level tokenizer holds at least one) plus the chat template, and the whole output cap.
        prompt = prompt_tokens_max(msgs)
        worst = (prompt * prices[1] + model.max_tokens * prices[2]) / 1e6
        if spent + worst > max_usd:
            raise Stop(3, "spending limit: %.4f of %.4f USD spent, the next call could cost %.4f" % (
                spent, max_usd, worst))
        if last_call is not None and clock() - last_call < pause:
            sleep(max(0.0, min(pause - (clock() - last_call), max_minutes * 60 - (clock() - start))))
        left = max_minutes * 60 - (clock() - start)
        if left < deepseek_review.MIN_ATTEMPT:
            raise Stop(3, "time limit (%g min): too little left for a call" % max_minutes)
        last_call = clock()
        content, usage = model.ask(msgs, left)
        cost = usage_cost(usage, prices)
        spent += cost
        tally["usd"] = spent
        record = {"event": "turn", "turn": turn, "call": calls,
                  "state": text_of(msgs[1]["content"]).split("State now:\n", 1)[1].split("\n(no screenshot", 1)[0],
                  "answer": content, "usage": usage, "usd": round(cost, 6), "usd_total": round(spent, 6)}
        if capture is not None:
            record["screenshot"] = shot.describe() if shot is not None else "none: " + no_shot
            if shot_file:
                record["screenshot_file"] = shot_file
        if swing:
            record["swing"] = swing
        try:
            why, do, frames, done, asked, remember, look, tell = parse_answer(content, max_frames)
        except ValueError as e:
            bad += 1
            history.append("turn %d: your answer was unusable (%s); answer only the JSON object" % (turn, e))
            record["error"] = str(e)
            log.write(record, "%3d  %-34s  unusable answer: %s  [%.4f USD]" % (turn, short(state)[:34], e, spent))
            if bad >= MAX_BAD:
                raise Stop(1, "%d unusable answers in a row" % bad)
            continue
        bad = 0
        record.update(why=why, do=do, done=done, frames=len(frames))
        if remember:
            record["remember"] = remember
            keep(turn, ["(your note) " + remember])
        if tell:
            record["to_others"] = tell
            keep(turn, ["(your note to the other players) " + tell])
        if notes is not None:
            failed = notes.write(player, turn, state, why, tell)
            if failed:
                record["notes_error"] = failed
        now = game.state()
        if clock() - start > max_minutes * 60:
            log.write(record, "%3d  %-34s  %s | %s (not played: time is up)  [%.4f USD]" % (
                turn, short(state)[:34], why, do or "-", spent))
            raise Stop(3, "time limit (%g min)" % max_minutes)
        # The answer is for the screen the model was shown. If that changed during the call (a cutscene, a timer,
        # the keyboard), or the save menu's cursor moved, the answer is not played: the model is asked again about
        # the new screen. Either way the save guard and --until look at the state from after the call.
        if screen(now) != screen(state) or now.get("save_menu") != state.get("save_menu"):
            note = "not played: the screen changed while you were thinking"
            history.append("turn %d: %s -> %s (%s)" % (turn, do or "(nothing)", change(state, now), note))
            record.update(result=change(state, now), played=0, note=note)
            keep(turn, events(state, now))
            learn_exits(seen_exits, [state, now])
            log.write(record, "%3d  %-34s  %s | %s (%s)  [%.4f USD]" % (
                turn, short(state)[:34], why, do or "-", note, spent))
            state = now
            continue
        state = now
        if met(state, until):
            continue
        if done and until and not why.lower().startswith("give up"):
            # The program judges the goal: a "done" before --until is met is told back to the model.
            early += 1
            if early >= MAX_EARLY_DONE:
                log.write(record, "%3d  %-34s  %s (done, but --until is not met)  [%.4f USD]" % (
                    turn, short(state)[:34], why or "done", spent))
                raise Stop(1, "the model said %d times that the goal is reached, but --until %s is not met" % (
                    early, until_text(until)))
            done = False
            history.append("turn %d: you answered done, but the program's goal check (the state has %s) is not met "
                           "yet, so the goal is not reached; keep going" % (turn, until_text(until)))
        if done:
            log.write(record, "%3d  %-34s  %s  [%.4f USD]" % (turn, short(state)[:34], why or "done", spent))
            if why.lower().startswith("give up"):
                raise Stop(1, "the model gave up: %s" % why)
            return 0, "the model says the goal is reached: %s" % why
        before = state
        seen = []
        if frames:
            state, played, note = play_turn(game, frames, state, allow_save, until, seen)
        else:
            played, note = 0, ""
        refused += note.startswith("refused")
        keep(turn, events(before, state, seen) + (["the save guard refused confirm (%s)" % save_risk(before)]
                                            if note.startswith("refused") else []))
        learn_exits(seen_exits, [before] + seen + [state])
        w = walked(frames[:played], before, state)
        if w and (walk is None or w[0] >= walk[0]):
            walk = w
        what = change(before, state)
        if asked > max_frames:
            note = (note + "; " if note else "") + "cut to %d frames" % max_frames
        if look and not note.startswith("refused"):
            note = (note + "; " if note else "") + "stopped at look: the steps after it were not played"
        history.append("turn %d: %s -> %s%s" % (turn, do or "(nothing)", what, " (%s)" % note if note else ""))
        record.update(result=what, played=played, note=note)
        log.write(record, "%3d  %-34s  %s | %s -> %s%s  [%.4f USD]" % (
            turn, short(before)[:34], why, do or "-", what, " (%s)" % note if note else "", spent))
        if refused >= MAX_REFUSED:
            raise Stop(1, "the save guard refused confirm %d times: this goal needs --allow-save" % refused)


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("goal", help='in plain words, e.g. "walk to the save point and save"')
    ap.add_argument("--port", type=int, default=agent_client.DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for the game's answer")
    ap.add_argument("--model", default="flash")
    ap.add_argument("--reasoning", default="none", choices=sorted(MAX_TOKENS))
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--max-usd", type=float, default=0.5)
    ap.add_argument("--max-minutes", type=float, default=20.0)
    ap.add_argument("--max-frames", type=int, default=150)
    ap.add_argument("--pause", type=float, default=0.5)
    ap.add_argument("--no-auto-confirm", action="store_true")
    ap.add_argument("--allow-save", action="store_true")
    ap.add_argument("--until", default=None)
    ap.add_argument("--key-file", default=DEFAULT_KEY_FILE)
    ap.add_argument("--guide", default=DEFAULT_GUIDE)
    ap.add_argument("--log", default=None)
    ap.add_argument("--prompt", action="store_true")
    ap.add_argument("--fake", metavar="FILE")
    ap.add_argument("--no-vision", action="store_true")
    ap.add_argument("--screenshot-cmd", default=None)
    ap.add_argument("--window", default="DELTARUNE")
    ap.add_argument("--window-pid", type=int, default=None)
    ap.add_argument("--screen-grab", action="store_true")
    ap.add_argument("--frames-dir", default=None)
    ap.add_argument("--notes", default=None)
    args = ap.parse_args(argv)
    if args.model.lower() not in MODELS:
        ap.error("unknown model %s: flash or pro" % args.model)
    args.model = MODELS[args.model.lower()]
    if args.max_steps < 1 or args.max_usd <= 0 or args.max_minutes <= 0 or not 30 <= args.max_frames <= 3600:
        ap.error("--max-steps, --max-usd and --max-minutes must be above 0, --max-frames 30 to 3600")
    if not args.goal.strip():
        ap.error("the goal is empty")
    if args.guide.lower() == "none":
        args.guide = ""
    try:
        read_guide(args.guide)
    except OSError as e:
        ap.error("cannot read the guide: %s" % e)
    try:
        args.until = parse_until(args.until) if args.until else None
    except ValueError as e:
        ap.error(str(e))
    return args


def main(argv, env=os.environ, out=sys.stdout, http=deepseek_review.http_json):
    args = parse_args(argv)
    goal = " ".join(args.goal.split())
    try:
        if args.fake:
            model = FakeModel.from_file(args.fake)
        elif args.prompt:
            model = None
        else:
            model = DeepSeek(load_key(env, args.key_file), args.model, args.reasoning, http)
    except Stop as e:
        print("error: %s" % e.why, file=sys.stderr)
        return e.code
    except OSError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    path = args.log or ("" if args.prompt else
                        "deepseek-player-%s.jsonl" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    capture, vision = None, "off (--no-vision)"
    if not args.no_vision:
        if args.model not in VISION_MODELS:
            vision = "off (%s does not take images)" % args.model
        else:
            try:
                capture = screen_capture.make_capture(args.screenshot_cmd, args.window, args.window_pid,
                                                       args.screen_grab)
            except screen_capture.CaptureError as e:
                vision = "off (%s)" % e
    if capture is not None and args.frames_dir:
        try:
            os.makedirs(args.frames_dir, exist_ok=True)
        except OSError as e:
            print("error: --frames-dir: %s" % e, file=sys.stderr)
            return 2
    notes = None
    if args.notes:
        try:
            open(args.notes, "a", encoding="utf-8").close()     # made now, so a wrong path is seen at once
        except OSError as e:
            print("error: --notes: %s" % e, file=sys.stderr)
            return 2
        notes = Notes(args.notes)
    game = agent_client.AgentClient(args.host, args.port, args.timeout)
    log = Log(path, out)
    code, why, tally = 0, "", {"usd": 0.0}
    try:
        game.connect()
        if args.prompt:
            shot, no_shot = None, ""
            if capture is not None:
                try:
                    shot = capture()
                except screen_capture.CaptureError as e:
                    no_shot = str(e)
            st = game.state()
            me = _num(st.get("player")) if _is_num(st.get("player")) else 1
            for m in messages(goal, args.max_frames, st, [], 1, args.max_steps, args.allow_save,
                              args.until, guide=args.guide, vision=capture is not None, shot=shot,
                              no_shot=no_shot, player=me, others=notes.lines(me) if notes else None):
                print("--- %s\n%s" % (m["role"], text_of(m["content"])), file=out)
                if not isinstance(m["content"], str):
                    print("[screenshot: %s]" % shot.describe(), file=out)
            if shot is not None and args.frames_dir:
                with open(os.path.join(args.frames_dir, "prompt.%s" % shot.mime.split("/")[1]), "wb") as f:
                    f.write(shot.data)
            return 0
        if capture is not None:
            # one screenshot now, so that a capture that cannot work is seen before the first call
            try:
                first = capture()
                vision = "on (%s, %s)" % (capture.what, first.describe())
                if getattr(capture, "note", ""):
                    vision += "; " + capture.note
            except screen_capture.CaptureError as e:
                vision = "on, but the first screenshot failed: %s" % e
        print("model %s, reasoning %s; limits: %d steps, %.4f USD, %g min; saving %s%s; vision %s; %slog %s" % (
            "fake" if args.fake else args.model, args.reasoning, args.max_steps, args.max_usd, args.max_minutes,
            "allowed" if args.allow_save else "refused", "; until " + until_text(args.until) if args.until else "",
            vision, "notes %s; " % args.notes if notes else "", path), file=out)
        code, why = play(game, model, goal, max_steps=args.max_steps, max_usd=args.max_usd,
                         max_minutes=args.max_minutes, max_frames=args.max_frames, pause=args.pause,
                         prices=PRICES.get(args.model, PRICES["deepseek-v4-pro"]),
                         auto_confirm=not args.no_auto_confirm, allow_save=args.allow_save,
                         until=args.until, log=log, tally=tally, guide=args.guide, capture=capture,
                         frames_dir=args.frames_dir, notes=notes)
    except Stop as e:
        code, why = e.code, e.why
    except agent_client.AgentError as e:
        code, why = 1, "game: %s" % e
    except RuntimeError as e:
        code, why = 1, "DeepSeek: %s" % e
    except KeyboardInterrupt:
        code, why = 130, "stopped with Ctrl+C"
    finally:
        if game.sock is not None:
            try:
                game.clear()
            except (agent_client.AgentError, OSError):
                pass
            game.close()
        if not args.prompt:
            log.write({"event": "end", "code": code, "why": why, "usd_total": round(tally["usd"], 6)},
                      "end: %s; %.4f USD at peak prices (exit %d)" % (why, tally["usd"], code))
        log.close()
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

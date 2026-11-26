"""tools/deepseek_player.py without the game and without DeepSeek: a small game in Python that speaks the Agent API
on a local port (a player who walks, a save point with a dialogue and a choice), a fake model, and a fake HTTP
layer in place of api.deepseek.com. The player against the real drnet.dll is part of `native/test_agent.py solo`."""
import base64
import contextlib
import io
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import agent_client  # noqa: E402
import deepseek_player as p  # noqa: E402
import screen_capture  # noqa: E402

KEY = "sk-test0123456789abcdef"
PRINT_FILE = "import sys; sys.stdout.buffer.write(open(sys.argv[1], 'rb').read())"


def process_alive(pid):
    """Whether process pid still runs (a zombie waiting to be reaped counts as stopped)."""
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out.split()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def kill_process(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def shell(*args):
    """A command line for the shell that subprocess uses here, each argument kept whole (paths with spaces)."""
    return subprocess.list2cmdline(args) if os.name == "nt" else " ".join(shlex.quote(a) for a in args)


class FakeGame:
    """The Agent API on 127.0.0.1, one connection at a time, with a tiny world shaped like the real save point:
    right/left move the player 3 px a frame. Confirm next to the save point at x=90 opens a box whose text types
    for 20 frames (confirm does nothing meanwhile) and then waits for confirm; confirm opens the save menu (stage 0,
    cursor on Save). Confirm on Save goes to the file slots (stage 1); confirm on slot 0 to 2 saves there (stage 2),
    confirm on slot 3 (Return) goes back; cancel closes the menu, confirm in stage 2 closes it."""

    SAVE_X = 90
    TYPING = 20

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.x, self.y, self.frame = 0, 0, 0
        self.dialogue = None        # [text, frames of typing left]
        self.menu = None            # the save_menu dict while the menu is open
        self.saved = None           # the slot saved to
        self.choice = None          # unused by the world; a test may set it
        self.door = None            # a test may set an x from which on the player is in room_next
        self.last = 0
        self.inputs = []            # every input command's steps, as the game got them
        self.cleared = 0
        threading.Thread(target=self.serve, daemon=True).start()

    def state(self):
        d = self.dialogue
        room = "room_next" if self.door is not None and self.x >= self.door else "room_test"
        s = {"frame": self.frame, "chapter": 1, "room": room, "player": 1, "battle": False, "menuno": -1,
             "interact": 0 if d is None and self.menu is None else 1,
             "players_pos": [{"player": 1, "object": "obj_mainchara", "name": "kris", "x": self.x, "y": self.y,
                              "facing": 1}],
             "dialogue": [] if d is None else [{"text": d[0], "shown": len(d[0]) * (self.TYPING - d[1]) // self.TYPING,
                                                "length": len(d[0]), "waiting": d[1] == 0}],
             "choice": self.choice,
             "save_menu": None if self.menu is None else dict(self.menu),
             "nearby": [{"object": "obj_savepoint", "x": self.SAVE_X, "y": -10, "solid": True, "visible": True}]}
        s["can_move"] = s["interact"] == 0
        return s

    def apply(self, bits):
        pressed = bits & ~self.last
        self.last = bits
        self.frame += 1
        d, m = self.dialogue, self.menu
        if d is not None:
            if d[1] > 0:
                d[1] -= 1
            elif pressed & 16:
                self.dialogue = None
                self.menu = {"stage": 0, "x": 0, "y": 0, "slot": 0, "overwrite": 0, "overwrite_x": 0}
            return
        if m is not None:
            if pressed & 32 and m["stage"] < 2:
                self.menu = None
            elif m["stage"] == 0:
                m["x"] = 1 if pressed & 2 else 0 if pressed & 8 else m["x"]
                if pressed & 16:
                    if m["x"] == 0:
                        m["stage"] = 1
                    else:
                        self.menu = None
            elif m["stage"] == 1:
                if pressed & 1:
                    m["slot"] = min(3, m["slot"] + 1)
                elif pressed & 4:
                    m["slot"] = max(0, m["slot"] - 1)
                if pressed & 16:
                    if m["slot"] == 3:
                        m["stage"], m["slot"] = 0, 0
                    else:
                        self.saved, m["stage"] = m["slot"], 2
            elif pressed & 16:
                self.menu = None
            return
        self.x += 3 * bool(bits & 2) - 3 * bool(bits & 8)
        if pressed & 16 and abs(self.x - self.SAVE_X) <= 20:
            self.dialogue = ["* A save point. & * Determination.", self.TYPING]

    def serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            reader = agent_client.LineReader()
            with conn:
                while True:
                    try:
                        data = conn.recv(65536)
                    except OSError:
                        break
                    if not data:
                        break
                    for line in reader.feed(data):
                        for reply in self.answer(json.loads(line)):
                            conn.sendall(agent_client.encode_line(reply))

    def answer(self, msg):
        rid = msg.get("id")
        if msg["cmd"] == "state":
            return [{"type": "state", "id": rid, "state": self.state()}]
        if msg["cmd"] == "clear":
            self.cleared += 1
            return [{"type": "cleared", "id": rid}]
        self.inputs.append(msg["steps"])
        total = 0
        for step in msg["steps"]:
            bits = sum(1 << agent_client.AGENT_BUTTONS.index(b) for b in step["buttons"])
            for _ in range(step["frames"]):
                self.apply(bits)
            total += step["frames"]
        return [{"type": "queued", "id": rid, "frames": total}, {"type": "done", "id": rid, "state": self.state()}]

    def close(self):
        self.srv.close()


def a_frames(text):
    return agent_client.text_to_frames(text)


def answer(do="", why="", done=False, remember=None, note=None):
    obj = {"why": why, "do": do, "done": done}
    if remember is not None:
        obj["remember"] = remember
    if note is not None:
        obj["note"] = note
    return json.dumps(obj)


class Prompt(unittest.TestCase):
    def test_summary_of_a_full_state(self):
        g = FakeGame()
        self.addCleanup(g.close)
        g.x, g.dialogue = 70, ["* Hello &  there.", 0]
        text = p.summarize(g.state())
        self.assertIn("room=room_test", text)
        self.assertIn("can_move=false", text)
        self.assertIn("you kris at x=70 y=0 facing right", text)
        self.assertIn('dialogue (waits for confirm): "* Hello & there."', text)
        self.assertIn("nearby: obj_savepoint (save point) (+20,-10) solid", text)
        self.assertIn("interact=1: a dialogue, a cutscene or a save point's menu holds you", text)
        self.assertNotIn("menuno", text)       # the Dark World menu is closed: its old numbers are left out

    def test_summary_of_the_stand_in_game_and_of_nulls(self):
        s = {"frame": 3, "mode": "solo", "player": 1, "players_pos": [{"player": 1, "x": 30, "y": 0, "confirm": 2}]}
        self.assertEqual(p.summarize(s), "you at x=30 y=0")
        s = {"room": None, "chapter": 2, "dialogue": None, "choice": None, "nearby": None, "players_pos": None,
             "desync": -1}
        self.assertEqual(p.summarize(s), "chapter=2")

    def test_save_menu(self):
        s = {"save_menu": {"stage": 0, "x": 0, "y": 0, "slot": 0, "overwrite": 0, "overwrite_x": 0}}
        self.assertIn('save point menu (open): the main box (Save, Return, Storage, Recruits), cursor on Save '
                      '{"stage":0,"x":0,"y":0,', p.summarize(s))
        self.assertNotIn("save point menu", p.summarize({"save_menu": None, "room": "r"}))
        words = lambda **k: p.save_menu_text(dict({"stage": 0, "x": 0, "y": 0, "slot": 0, "overwrite": 0,  # noqa
                                                   "overwrite_x": 0}, **k))
        self.assertEqual(words(x=1, y=1), "the main box (Save, Return, Storage, Recruits), cursor on Recruits")
        self.assertEqual(words(x=2), "the main box: the message that the game was saved waits for confirm")
        self.assertEqual(words(stage=1, slot=2), "the list of file slots, cursor on file slot 2")
        self.assertEqual(words(stage=1, slot=3.0), "the list of file slots, cursor on Return")
        self.assertEqual(words(stage=1, overwrite=1, overwrite_x=1),
                         "the question whether to overwrite the file, cursor on Return")

    def test_game_numbers_in_words(self):
        text = p.summarize({"interact": 5, "menuno": 0, "submenu": 2})
        self.assertEqual(text, "interact=5: the Dark World menu is open; menuno=0: its top row of icons, submenu=2")
        self.assertIn("menuno=3: one of its pages", p.summarize({"interact": 5.0, "menuno": 3}))
        self.assertEqual(p.summarize({"interact": 0, "menuno": -1}), "interact=0: free to walk")
        self.assertEqual(p.summarize({"interact": 9}), "interact=9: the game's own number")
        self.assertIn("battle=true: a battle is on", p.summarize({"battle": True}))
        self.assertNotIn("battle", p.summarize({"battle": False, "room": "r"}))

    def test_events_for_the_done_list(self):
        free = {"room": "a", "interact": 0, "menuno": 0, "save_menu": None, "choice": None, "dialogue": []}
        menu = dict(free, interact=5)
        self.assertEqual(p.events(free, menu), ["opened the Dark World menu"])
        self.assertEqual(p.events(menu, dict(menu, menuno=2)), ["Dark World menu: opened its page menuno=2"])
        self.assertEqual(p.events(menu, dict(free, menuno=-1)), ["closed the Dark World menu"])
        self.assertEqual(p.events(free, dict(free, room="b")), ["entered room b (from a)"])
        sm = {"stage": 0, "x": 0, "y": 0, "slot": 0, "overwrite": 0, "overwrite_x": 0}
        talk = dict(free, interact=1, dialogue=[{"text": "* A save point.", "waiting": True}])
        self.assertEqual(p.events(free, talk), ['dialogue: "* A save point."'])
        self.assertEqual(p.events(talk, dict(free, interact=1, save_menu=sm)), ["opened the save point's menu"])
        self.assertEqual(p.events(dict(free, save_menu=sm), dict(free, save_menu=dict(sm, stage=1))),
                         ["save point's menu: chose Save, the file slots are shown"])
        self.assertEqual(p.events(dict(free, save_menu=sm), free), ["closed the save point's menu"])
        choice = dict(free, choice={"options": ["Yes", "No"], "selected": 1})
        self.assertEqual(p.events(free, choice), ['a choice appeared: ["Yes", "No"]'])
        self.assertEqual(p.events(choice, free), ['the choice closed (last selected: "No")'])
        # moved and confirmed in one turn: the selection is taken from the last state that still had the choice
        moved = dict(free, choice={"options": ["Yes", "No"], "selected": 0})
        self.assertEqual(p.events(moved, free, [choice]), ['the choice closed (last selected: "No")'])
        self.assertEqual(p.events(moved, free, [free]), ['the choice closed (last selected: "Yes")'])
        self.assertEqual(p.events(free, dict(free, battle=True)), ["a battle started"])
        self.assertEqual(p.events(free, free), [])

    def test_long_dialogue_and_many_objects_are_cut(self):
        s = {"player": 1, "players_pos": [{"player": 1, "x": 0, "y": 0}],
             "dialogue": [{"text": "a" * 5000, "waiting": False}],
             "nearby": [{"object": "obj_%d" % i, "x": i, "y": 0} for i in range(30)]}
        text = p.summarize(s)
        self.assertLess(len(text), 900)
        self.assertIn("obj_11", text)
        self.assertNotIn("obj_12", text)

    def test_system_prompt_is_the_same_every_turn(self):
        a = p.messages("save", 150, {"room": "a"}, [], 1, 40)
        b = p.messages("save", 150, {"room": "b"}, ["turn 1: 20 right -> moved (+60,+0)"], 2, 40)
        self.assertEqual(a[0], b[0])
        self.assertIn("Goal: save", a[0]["content"])
        self.assertIn("About the game:", a[0]["content"])
        self.assertIn("## The Dark World menu", a[0]["content"])
        self.assertIn("Done so far in this run (kept for the whole run):\n(nothing yet)", a[1]["content"])
        c = p.messages("save", 150, {"room": "c"}, ["turn %d: wait 1 -> nothing changed" % i for i in range(1, 41)],
                       41, 60, done=["turn 3: opened the save point's menu"])
        self.assertEqual(a[0], c[0])
        self.assertIn("turn 3: opened the save point's menu", c[1]["content"])
        self.assertIn("turn 11: wait 1", c[1]["content"])        # the last 30 turns
        self.assertNotIn("turn 10: wait 1", c[1]["content"])
        self.assertNotIn("About the game", p.messages("save", 150, {}, [], 1, 40, guide="")[0]["content"])
        self.assertIn("150 frames", a[0]["content"])
        self.assertIn("turn 1: 20 right", b[1]["content"])

    def test_exits_in_the_summary(self):
        s = {"room": "r", "player": 1, "room_size": {"w": 640, "h": 480},
             "players_pos": [{"player": 1, "x": 300, "y": 200}],
             "exits": [{"object": "obj_doorA", "x": 0, "y": 220, "left": -10, "top": 200, "right": 10, "bottom": 280,
                        "edge": "left", "to": None, "visible": False},
                       {"object": "obj_doorX", "x": 310, "y": 470, "left": 300, "top": 470, "right": 340,
                        "bottom": 490, "edge": "bottom", "to": "room_b", "visible": False},
                       {"object": "obj_door_old"}]}
        text = p.summarize(s)
        self.assertIn("room=r room_size=640x480", text)
        self.assertIn("exits:\n  obj_doorA at the left edge, box x=-10..10 y=200..280, (-300,+40) left\n"
                      "  obj_doorX at the bottom edge, box x=300..340 y=470..490, (+20,+280) down, "
                      "leads to room_b (going by its variables)", text)
        self.assertNotIn("obj_door_old", text)       # no place: left out
        self.assertIn("exits: no door objects in this room", p.summarize({"room": "r", "exits": []}))
        self.assertNotIn("exits", p.summarize({"room": "r"}))     # an older game build without the key
        self.assertEqual([p.compass(*d) for d in ((5, -5), (-100, 30), (100, 80), (10, -200), (-60, 60))],
                         ["here", "left", "down-right", "up", "down-left"])

    def test_where_this_run_came_in_and_left_goes_with_the_exits(self):
        a = {"room": "a", "player": 1, "players_pos": [{"player": 1, "x": 20, "y": 230}]}
        b = {"room": "b", "player": 1, "players_pos": [{"player": 1, "x": 600, "y": 100}]}
        seen = p.learn_exits({}, [dict(a, players_pos=[{"player": 1, "x": 200, "y": 230}]), a, b, b])
        self.assertEqual(seen, {"a": [(20, 230, "you left here to b")], "b": [(600, 100, "you came in here from a")]})
        p.learn_exits(seen, [b, dict(a, players_pos=[{"player": 1, "x": 30, "y": 240}])])
        self.assertEqual(seen["a"], [(20, 230, "you left here to b"), (30, 240, "you came in here from b")])
        p.learn_exits(seen, [a, b])        # the same way again: one entry a text, the newest place
        self.assertEqual(seen["a"], [(30, 240, "you came in here from b"), (20, 230, "you left here to b")])
        s = dict(a, players_pos=[{"player": 1, "x": 300, "y": 200}],
                 exits=[{"object": "obj_doorA", "left": -10, "top": 200, "right": 10, "bottom": 280, "edge": "left"}])
        text = p.summarize(s, seen["a"] + [(320, 10, "you came in here from c")])
        self.assertIn("obj_doorA at the left edge, box x=-10..10 y=200..280, (-300,+40) left; you came in here "
                      "from b; you left here to b", text)
        self.assertIn("no door object: you came in here from c (+20,-190) up", text)
        # no exits key in the state: what the run saw is still shown
        self.assertIn("exits:\n  no door object: you left here to a (+0,+0) here\n"
                      "  no door object: you came in here from a (+0,+0) here", p.summarize(b, seen["b"]))

    def test_walking_back_and_forth_is_told(self):
        swing = [(0, 100), (120, 100), (10, 104), (130, 100), (0, 96), (125, 100), (5, 100), (120, 102)]
        warn = p.back_and_forth(swing)
        self.assertIn("in your last 8 turns you walked back and forth between x=0 and x=130 (y about 100)", warn)
        self.assertIn("try up or down", warn)
        self.assertIsNone(p.back_and_forth(swing[:7]))                          # too few turns yet
        self.assertIsNone(p.back_and_forth([(x, y * 30) for y, (x, _) in enumerate(swing)]))   # it goes down too
        self.assertIsNone(p.back_and_forth([(i * 40, 100) for i in range(8)]))   # one way only
        self.assertIsNone(p.back_and_forth([(i % 2 * 5, 100) for i in range(8)]))   # small steps on one spot
        up_down = p.back_and_forth([(y, x) for x, y in swing])
        self.assertIn("between y=0 and y=130 (x about 100)", up_down)
        self.assertIn("try left or right", up_down)

    def test_camera_rule_for_player_2(self):
        one = p.messages("go", 150, {"player": 1}, [], 1, 40, vision=True, no_shot="x", player=1)[0]["content"]
        two = p.messages("go", 150, {"player": 2}, [], 1, 40, vision=True, no_shot="x", player=2)[0]["content"]
        self.assertIn("The camera follows you", one)
        self.assertIn("The camera follows player 1 (Kris), not you (player 2)", two)

    def test_change(self):
        a = {"room": "r", "player": 1, "players_pos": [{"player": 1, "x": 0, "y": 0}], "can_move": True}
        b = {"room": "r", "player": 1, "players_pos": [{"player": 1, "x": 30, "y": -6}], "can_move": False,
             "choice": {"options": []}}
        self.assertEqual(p.change(a, a), "nothing changed")
        self.assertEqual(p.change(a, b), "moved (+30,-6), can_move True -> False, choice appeared")


class NotesFile(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "notes.jsonl")
        self.now = 1000.0

    def notes(self):
        return p.Notes(self.path, clock=lambda: self.now)

    def state(self, player, x, y, room="r"):
        return {"room": room, "player": player, "players_pos": [{"player": player, "x": x, "y": y}]}

    def test_each_player_sees_the_others(self):
        one, two = self.notes(), self.notes()
        self.assertEqual(one.lines(1), [])              # no file yet
        self.assertEqual(two.write(2, 3, self.state(2, 50.4, 60), "walk left", ""), "")
        self.now += 5
        two.write(2, 4, self.state(2, 10, 60), "go down the path", "the way on is at the bottom left")
        one.write(1, 7, self.state(1, 300, 200), "walk right", "nothing on the right side")
        self.now += 120
        self.assertEqual(one.lines(1), [
            'player 2, 2 min ago (its turn 4), in r at x=10 y=60, doing: "go down the path"',
            '  player 2 wrote 2 min ago, in r: "the way on is at the bottom left"'])
        self.assertEqual(two.lines(2), [
            'player 1, 2 min ago (its turn 7), in r at x=300 y=200, doing: "walk right"',
            '  player 1 wrote 2 min ago, in r: "nothing on the right side"'])
        with open(self.path, encoding="utf-8") as f:
            first = json.loads(f.readline())
        self.assertEqual(first, {"player": 2, "turn": 3, "time": 1000.0, "room": "r", "x": 50, "y": 60,
                                 "doing": "walk left", "note": ""})

    def test_old_broken_and_many_lines(self):
        n = self.notes()
        n.write(2, 1, self.state(2, 0, 0), "old", "an old note")
        self.now += p.NOTES_MAX_AGE + 1
        self.assertEqual(n.lines(1), [])                # older than an hour
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"player": 2, "turn": 2, "ti\n[1, 2]\nnot json\n')
        for i in range(6):
            n.write(2, 10 + i, self.state(2, i, 0), "step %d" % i, "note %d" % i)
        lines = n.lines(1)
        self.assertEqual(lines[0], 'player 2, 0 s ago (its turn 15), in r at x=5 y=0, doing: "step 5"')
        self.assertEqual([line.split(": ", 1)[1] for line in lines[1:]],
                         ['"note 2"', '"note 3"', '"note 4"', '"note 5"'])    # the latest NOTES_SHOWN
        # only the end of a big file is read, and the line cut at its start is skipped
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(("x" * 1000 + "\n") * (p.NOTES_TAIL // 1000 + 2))
        n.write(2, 30, self.state(2, 1, 1), "late", "")
        self.assertEqual(n.lines(1), ['player 2, 0 s ago (its turn 30), in r at x=1 y=1, doing: "late"'])

    def test_unwritable_file(self):
        bad = p.Notes(os.path.join(self.path, "no", "such", "dir.jsonl"))
        self.assertIn("cannot write the notes file", bad.write(1, 1, {}, "x", ""))
        self.assertEqual(bad.lines(1), [])


class Vision(unittest.TestCase):
    def shot(self):
        return screen_capture.Shot(screen_capture.png(2, 1, b"\x01\x02\x03\x04\x05\x06"), "image/png", 2, 1)

    def test_the_screenshot_goes_into_the_user_message(self):
        m = p.messages("go", 150, {"room": "r"}, [], 1, 40, vision=True, shot=self.shot())
        self.assertIn('put the step\n"look" among your steps', m[0]["content"])
        content = m[1]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("State now:\nroom=r", content[0]["text"])
        url = content[1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), self.shot().data)
        self.assertEqual(p.text_of(content), content[0]["text"])
        self.assertEqual(p.prompt_tokens_max(m), len(m[0]["content"].encode()) + len(content[0]["text"].encode())
                         + 2 * p.TEMPLATE_TOKENS + p.IMAGE_TOKENS)
        # the system prompt is the same with or without a picture this turn, so it stays cached
        n = p.messages("go", 150, {"room": "r"}, [], 2, 40, vision=True, no_shot="the game window is minimized")
        self.assertEqual(m[0], n[0])
        self.assertTrue(n[1]["content"].endswith("(no screenshot this turn: the game window is minimized)"))
        off = p.messages("go", 150, {"room": "r"}, [], 1, 40)
        self.assertNotIn("Screenshot:", off[0]["content"])
        self.assertIsInstance(off[1]["content"], str)

    def test_png_and_its_size(self):
        data = screen_capture.png(3, 2, bytes(range(18)))
        self.assertEqual(screen_capture.image_info(data), ("image/png", 3, 2))
        chunks, i = {}, 8
        while i < len(data):
            n = int.from_bytes(data[i:i + 4], "big")
            chunks[data[i + 4:i + 8]] = data[i + 8:i + 8 + n]
            i += 12 + n
        self.assertEqual(zlib.decompress(chunks[b"IDAT"]), b"\x00" + bytes(range(9)) + b"\x00" + bytes(range(9, 18)))
        self.assertEqual(screen_capture.image_info(b"\xff\xd8\xff\xe0rest")[0], "image/jpeg")
        with self.assertRaises(screen_capture.CaptureError):
            screen_capture.image_info(b"hello")

    def test_window_area_and_scaling(self):
        self.assertEqual(screen_capture.game_area(1920, 1080), (240, 0, 1440, 1080))
        self.assertEqual(screen_capture.game_area(640, 480), (0, 0, 640, 480))
        self.assertEqual(screen_capture.game_area(800, 800), (0, 100, 800, 600))
        self.assertEqual(screen_capture.fit(1440, 1080), (640, 480))
        self.assertEqual(screen_capture.fit(320, 240), (320, 240))
        # 4x2 BGRA, the right half kept and halved: one pixel a row, B and R swapped
        src = bytes([1, 2, 3, 0, 4, 5, 6, 0, 7, 8, 9, 0, 10, 11, 12, 0,
                     13, 14, 15, 0, 16, 17, 18, 0, 19, 20, 21, 0, 22, 23, 24, 0])
        self.assertEqual(screen_capture.bgra_to_rgb(src, 16, (2, 0, 2, 2), (1, 2)), bytes([9, 8, 7, 21, 20, 19]))

    def test_command_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "shot.png")
            with open(path, "wb") as f:
                f.write(self.shot().data)
            shot = screen_capture.CommandCapture(shell(sys.executable, "-c", PRINT_FILE, path))()
            self.assertEqual((shot.mime, shot.width, shot.height, shot.data), ("image/png", 2, 1, self.shot().data))
        with self.assertRaises(screen_capture.CaptureError):
            screen_capture.CommandCapture(shell(sys.executable, "-c", "print('not an image')"))()
        with self.assertRaisesRegex(screen_capture.CaptureError, r"\(exit 3\)"):
            screen_capture.CommandCapture(shell(sys.executable, "-c", "import sys; sys.exit(3)"))()

    def test_command_capture_timeout_stops_its_children(self):
        # the command starts a child that keeps the output pipe open; the timeout must end the capture and stop
        # the child too
        hang = ("import subprocess, sys, time; "
                "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                "open(sys.argv[1], 'w').write(str(c.pid)); time.sleep(60)")
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = os.path.join(tmp, "child.pid")
            result = []

            def capture():
                try:
                    screen_capture.CommandCapture(shell(sys.executable, "-c", hang, pid_file), timeout=1)()
                    result.append(None)
                except screen_capture.CaptureError as e:
                    result.append(e)

            t = threading.Thread(target=capture, daemon=True)
            t.start()
            t.join(20)      # a hang fails the test instead of holding up the whole run
            with open(pid_file) as f:
                child = int(f.read())
            try:
                self.assertFalse(t.is_alive(), "the capture did not return after its timeout")
                self.assertRegex(str(result[0]), "more than 1 s")
                for _ in range(50):
                    if not process_alive(child):
                        break
                    time.sleep(0.1)
                self.assertFalse(process_alive(child), "the command's child %d still runs" % child)
            finally:
                if process_alive(child):
                    kill_process(child)


class Answers(unittest.TestCase):
    def test_plain_json(self):
        why, do, frames, done, asked, note, look, tell = p.parse_answer(answer("20 right; tap z", "walk"), 150)
        self.assertFalse(look)
        self.assertEqual((note, tell), ("", ""))
        self.assertEqual((why, do, done, asked), ("walk", "20 right; tap z", False, 22))
        self.assertEqual(len(frames), 22)

    def test_json_inside_other_words(self):
        why, do, frames, done = p.parse_answer('Sure:\n```json\n{"why":"ok","do":"5 up","done":true}\n```', 150)[:4]
        self.assertEqual((do, done, len(frames)), ("5 up", True, 5))

    def test_look_stops_the_steps(self):
        a = p.parse_answer(answer("20 right; look; 40 up"), 150)
        self.assertTrue(a.look)
        self.assertEqual(a.do, "20 right; look; 40 up")
        self.assertEqual(a.frames, a_frames("20 right"))
        self.assertEqual(p.split_look("10 up\n LOOK # see the room\nwait 5"), ("10 up", True))
        self.assertEqual(p.split_look("look"), ("", True))
        self.assertEqual(p.split_look("20 right"), ("20 right", False))
        # "#" starts a comment as in the Agent API: a "look" inside one is no step
        self.assertEqual(p.split_look("20 right # note; look\n40 up"), ("20 right # note; look\n40 up", False))
        self.assertEqual(p.split_look("20 right # then look\nlook; 40 up"), ("20 right # then look", True))
        self.assertEqual(p.parse_answer(answer("look"), 150).frames, [])
        self.assertEqual(p.parse_answer(answer("look; 99999 right"), 150).frames, [])    # never played

    def test_frames_are_cut(self):
        self.assertEqual(len(p.parse_answer(answer("10s right"), 150)[2]), 150)
        self.assertEqual(p.parse_answer(answer("10s right"), 150)[4], 300)

    def test_huge_answers_are_refused_before_they_are_built(self):
        for do in ("999999999 right", "wait 99999999", "tap z 100000 100000", "1 right; 2000000s left"):
            with self.assertRaises(ValueError, msg=do) as cm:
                p.parse_answer(answer(do), 150)
            self.assertIn("send at most 150", str(cm.exception))
        for do in ("wait 1e308s", "1e400s right", "wait infs", "tap z 2 1e308s"):
            with self.assertRaises(ValueError, msg=do) as cm:
                p.parse_answer(answer(do), 150)
            self.assertIn("too many frames", str(cm.exception))
        self.assertEqual(p.frames_in("20 right; tap z 2 3 # 9 up\nwait 1.5s; tap x"), 20 + 8 + 45 + 2)
        self.assertEqual(p.frames_in("20 rihgt; nonsense here too"), 20)

    def test_unusable(self):
        for text in ("walk right", "[1, 2]", answer("20 teleport"), answer("20 rihgt"), '{"do": 5}', ""):
            with self.assertRaises(ValueError, msg=text):
                p.parse_answer(text, 150)

    def test_cost(self):
        usage = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200,
                 "completion_tokens": 50}
        self.assertAlmostEqual(p.usage_cost(usage, (0.006, 0.3, 1.2)), (800 * 0.006 + 200 * 0.3 + 50 * 1.2) / 1e6)
        self.assertAlmostEqual(p.usage_cost({"prompt_tokens": 100, "completion_tokens": 10}, (1, 2, 3)), 230 / 1e6)


class Key(unittest.TestCase):
    def test_environment_first_then_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "deepseek.env")
            with open(path, "w") as f:
                f.write("# my key\nexport DEEPSEEK_API_KEY='sk-fromfile'\n")
            self.assertEqual(p.load_key({"DEEPSEEK_API_KEY": "sk-env"}, path), "sk-env")
            self.assertEqual(p.load_key({}, path), "sk-fromfile")
            with self.assertRaises(p.Stop) as cm:
                p.load_key({}, os.path.join(tmp, "missing"))
            self.assertEqual(cm.exception.code, 2)

    def test_key_file_forms(self):
        self.assertEqual(p.key_from_text("DEEPSEEK_API_KEY=sk-a\n"), "sk-a")
        self.assertEqual(p.key_from_text('OTHER=1\nDEEPSEEK_API_KEY = "sk-b"\n'), "sk-b")
        self.assertEqual(p.key_from_text("\nsk-bare\n"), "sk-bare")
        self.assertEqual(p.key_from_text("OTHER=1\n"), "")


class Http:
    """Stands in for deepseek_review.http_json: records the requests, answers from a list."""

    def __init__(self, answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, method, url, headers, body=None, timeout=60):
        self.requests.append((method, url, headers, body))
        content = self.answers.pop(0)
        if isinstance(content, tuple):
            return content
        return 200, {"choices": [{"message": {"content": content}}],
                     "usage": {"prompt_tokens": 900, "prompt_cache_hit_tokens": 640, "prompt_cache_miss_tokens": 260,
                               "completion_tokens": 40}}


def run_main(game, args, env=None, http=None):
    out = io.StringIO()
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "log.jsonl")
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            # no screenshots unless a test asks for them: on Windows with the game open the window would be captured
            vision = [] if "--screenshot-cmd" in args else ["--no-vision"]
            code = p.main(["--port", str(game.port), "--pause", "0", "--log", log,
                           "--key-file", os.path.join(tmp, "none")] + vision + args, env or {}, out, http or Http([]))
        with open(log, encoding="utf-8") as f:
            records = [json.loads(line) for line in f]
    return code, out.getvalue() + err.getvalue(), records


class Loop(unittest.TestCase):
    def setUp(self):
        self.game = FakeGame()
        self.addCleanup(self.game.close)

    def fake(self, answers, *args):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "answers.txt")
            with open(path, "w") as f:
                f.write("\n".join(answers) + "\n")
            return run_main(self.game, ["walk to the save point"] + list(args) + ["--fake", path])

    def test_save_with_the_fake_model(self):
        code, out, records = self.fake([answer("30 right", "walk to the save point"), answer("tap confirm", "use it"),
                                        answer("tap confirm", "Save"), answer("tap confirm", "slot 0"),
                                        answer("tap confirm", "close"), answer("", "saved", True)], "--allow-save")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.game.saved, 0)
        self.assertEqual(self.game.x, 90)
        self.assertIsNone(self.game.menu)
        turns = [r for r in records if r["event"] == "turn"]
        self.assertEqual([r["do"] for r in turns],
                         ["30 right", "tap confirm", "wait 20", "tap confirm; wait 4", "tap confirm",
                          "tap confirm", "tap confirm", ""])
        self.assertEqual(turns[0]["result"], "moved (+90,+0)")
        self.assertIn("save point menu appeared", turns[3]["result"])
        self.assertEqual(records[-1]["event"], "end")
        self.assertIn("goal is reached: saved", out)
        self.assertEqual(self.game.cleared, 1)          # the queued input is dropped at the end

    def test_a_turn_stops_when_the_screen_changes(self):
        # The first run in the game: four confirms in one turn ran through the dialogue, the menu and a save.
        self.game.x = 90
        code, out, records = self.fake([answer("tap confirm 3 10; tap confirm 3 10; tap confirm 3 10")],
                                       "--allow-save", "--no-auto-confirm")
        self.assertEqual(code, 0, out)
        turn = [r for r in records if r["event"] == "turn"][0]
        self.assertEqual(turn["played"], 11)
        self.assertIn("dropped: the screen changed", turn["note"])
        self.assertIsNotNone(self.game.dialogue)
        self.assertIsNone(self.game.saved)

    def test_a_long_walk_stops_at_a_door(self):
        self.game.door = 45
        code, out, records = self.fake([answer("120 right")], "--max-frames", "150")
        self.assertEqual(code, 0, out)
        turn = [r for r in records if r["event"] == "turn"][0]
        self.assertEqual(turn["played"], p.PIECE)
        self.assertIn("the rest (90 frames) dropped", turn["note"])
        self.assertEqual(self.game.x, 3 * p.PIECE)

    def test_long_holds_are_cut_only_where_that_is_harmless(self):
        class Game:
            def __init__(self, state):
                self.st, self.sent = state, []

            def send_steps(self, steps):
                self.sent.append(sum(s["frames"] for s in steps))
                return self.st
        walk = {"room": "r", "can_move": True, "dialogue": [], "choice": None, "save_menu": None}
        menu = dict(walk, can_move=False, save_menu={"stage": 0, "x": 0})
        for state, text, sent in [(walk, "70 right", [30, 30, 10]), (walk, "wait 40", [30, 10]),
                                  (menu, "wait 40", [30, 10]), (menu, "40 right", [40]),
                                  (walk, "40 right+confirm", [40]), (walk, "20 right", [20])]:
            g = Game(state)
            _, played, note = p.play_turn(g, a_frames(text), state, False, None)
            self.assertEqual((g.sent, played, note), (sent, sum(sent), ""), text)

    def test_an_answer_is_not_played_when_the_screen_changed_during_the_call(self):
        model = p.FakeModel([answer("20 right"), answer("", "a box came", True)])
        asked = model.ask

        def ask(msgs, seconds=120):
            if not model.asked:
                self.game.dialogue = ["* A cutscene.", 0]       # appears while the model thinks
            return asked(msgs, seconds)
        model.ask = ask
        with agent_client.AgentClient(port=self.game.port) as game:
            code, why = p.play(game, model, "go", auto_confirm=False, pause=0)
        self.assertEqual(code, 0, why)
        self.assertEqual(self.game.x, 0)            # "20 right" was for the screen before the box
        self.assertEqual(len(model.asked), 2)
        self.assertIn("not played: the screen changed while you were thinking", model.asked[1][1]["content"])

    def test_a_cursor_moved_during_the_call_is_seen_by_the_save_guard(self):
        self.game.menu = {"stage": 1, "x": 0, "y": 0, "slot": 3, "overwrite": 0, "overwrite_x": 0}
        model = p.FakeModel([answer("tap confirm", "Return"), answer("tap confirm", "Return"), answer("tap cancel")])
        asked = model.ask

        def ask(msgs, seconds=120):
            if not model.asked:
                self.game.menu["slot"] = 2      # the keyboard moves the cursor while the model thinks
            return asked(msgs, seconds)
        model.ask = ask
        with agent_client.AgentClient(port=self.game.port) as game:
            code, why = p.play(game, model, "go", pause=0)
        self.assertEqual(code, 0, why)
        self.assertIsNone(self.game.saved)
        self.assertIn("not played: the screen changed", model.asked[1][1]["content"])
        self.assertIn("refused: confirm here saves to file slot 2", model.asked[2][1]["content"])

    def test_saving_is_refused_without_allow_save(self):
        http = Http([answer("30 right"), answer("tap confirm"), answer("tap confirm", "Save"),
                     answer("tap confirm", "slot 0"), answer("tap cancel"), answer("", "menu seen", True)])
        code, out, records = run_main(self.game, ["open the save menu"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0, out)
        self.assertIsNone(self.game.saved)
        self.assertIsNone(self.game.menu)
        refused = [r for r in records if "refused" in r.get("note", "")]
        self.assertEqual(len(refused), 1)
        self.assertIn("saves to file slot 0", refused[0]["note"])
        self.assertIn("refused: confirm here saves to file slot 0", http.requests[4][3]["messages"][1]["content"])
        self.assertIn("Saving to a file is not allowed", http.requests[0][3]["messages"][0]["content"])

    def test_second_refusal_ends_the_run(self):
        self.game.menu = {"stage": 1, "x": 0, "y": 0, "slot": 2, "overwrite": 0, "overwrite_x": 0}
        http = Http([answer("tap confirm", "save"), answer("tap confirm", "save again"), answer("", "x", True)])
        code, out, _ = run_main(self.game, ["save the game"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 1)
        self.assertIn("refused confirm 2 times: this goal needs --allow-save", out)
        self.assertIn("Do not try again", http.requests[1][3]["messages"][1]["content"])
        self.assertIsNone(self.game.saved)

    def test_a_box_with_a_long_pause_is_waited_for_in_one_turn(self):
        self.game.TYPING = 60
        self.game.x = 90
        self.game.apply(16)
        self.game.apply(0)
        with agent_client.AgentClient(port=self.game.port) as game:
            state, waited = p.wait_typing(game, game.state())
            self.assertTrue(state["dialogue"][0]["waiting"])
            self.assertEqual(waited, 60)
            self.game.apply = lambda bits: None     # the text stops growing: give up after WAIT_QUIET frames
            self.game.dialogue[1] = 5
            state, waited = p.wait_typing(game, game.state())
            self.assertEqual(waited, p.WAIT_QUIET)

    def test_overwrite_question_is_guarded_too(self):
        self.assertIn("overwrite", p.save_risk({"save_menu": {"stage": 1, "slot": 3, "overwrite": 1,
                                                              "overwrite_x": 0}}))
        self.assertIsNone(p.save_risk({"save_menu": {"stage": 1, "slot": 3, "overwrite": 1, "overwrite_x": 1}}))
        self.assertIsNone(p.save_risk({"save_menu": {"stage": 0, "x": 0, "y": 0, "slot": 0}}))
        self.assertIsNone(p.save_risk({"save_menu": None}))

    def test_until_ends_the_run_without_the_model(self):
        http = Http([answer("30 right"), answer("tap confirm 3 10")])
        code, out, records = run_main(self.game, ["open the save menu", "--until", "save_menu"],
                                      {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0, out)
        self.assertIn("--until save_menu is met", out)
        self.assertEqual(len(http.requests), 2)
        self.assertEqual(self.game.menu["stage"], 0)
        self.assertIn("it ends the run as soon as the state has save_menu",
                      http.requests[0][3]["messages"][0]["content"])
        self.assertIn("Goal check by the program (the state has save_menu): not met yet.",
                      http.requests[0][3]["messages"][1]["content"])

    def test_done_before_until_is_met_is_told_back(self):
        http = Http([answer("", "the menu is open", True), answer("30 right", "walk"), answer("", "done", True),
                     answer("", "done", True)])
        code, out, records = run_main(self.game, ["open the save menu", "--until", "save_menu"],
                                      {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 1)
        self.assertIn("said 3 times that the goal is reached, but --until save_menu is not met", out)
        self.assertIn("you answered done, but the program's goal check (the state has save_menu) is not met yet",
                      http.requests[1][3]["messages"][1]["content"])
        self.assertEqual(self.game.x, 90)       # the run went on after the first "done"

    def test_done_list_and_notes_stay_for_the_whole_run(self):
        # The save point's menu opens in turn 2; 35 turns later it is still in "done so far", though the last
        # turns hold only 30.
        answers = [answer("30 right", "walk", remember="the star is at x=90"), answer("tap confirm", "use it"),
                   answer("tap cancel", "close")] + [answer("wait 1")] * 35 + [answer("", "ok", True)]
        http = Http(answers)
        code, out, records = run_main(self.game, ["go", "--max-steps", "60"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0, out)
        last = http.requests[-1][3]["messages"][1]["content"]
        self.assertIn("turn 1: (your note) the star is at x=90", last)
        self.assertIn("opened the save point's menu", last)
        self.assertIn("closed the save point's menu", last)
        self.assertIn('dialogue: "* A save point. & * Determination."', last)
        self.assertNotIn("turn 2: tap confirm", last.split("Last turns:")[1])
        self.assertEqual(records[1]["remember"], "the star is at x=90")

    def screenshot_cmd(self, tmp):
        path = os.path.join(tmp, "shot.png")
        with open(path, "wb") as f:
            f.write(screen_capture.png(2, 1, b"\x01\x02\x03\x04\x05\x06"))
        return shell(sys.executable, "-c", PRINT_FILE, path)

    def test_a_screenshot_every_turn_and_look(self):
        with tempfile.TemporaryDirectory() as tmp:
            frames = os.path.join(tmp, "frames")
            http = Http([answer("30 right; look; 30 right", "walk, then look"), answer("", "ok", True)])
            code, out, records = run_main(self.game, ["go", "--screenshot-cmd", self.screenshot_cmd(tmp),
                                                      "--frames-dir", frames], {"DEEPSEEK_API_KEY": KEY}, http)
            self.assertEqual(code, 0, out)
            self.assertIn("vision on (command, 2x1 PNG", out)
            self.assertEqual(sorted(os.listdir(frames)), ["turn-001.png", "turn-002.png"])
        self.assertEqual(self.game.x, 90)       # the steps after "look" were not played
        for req in http.requests:
            msgs = req[3]["messages"]
            self.assertIn("Screenshot: each turn comes with a picture", msgs[0]["content"])
            self.assertTrue(msgs[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertIn("stopped at look: the steps after it were not played",
                      http.requests[1][3]["messages"][1]["content"][0]["text"])
        turns = [r for r in records if r["event"] == "turn"]
        self.assertTrue(turns[0]["screenshot"].startswith("2x1 PNG"))
        self.assertTrue(turns[0]["state"].startswith("chapter=1 room=room_test"))
        self.assertNotIn("base64", json.dumps(records))     # the log keeps no image

    def test_failed_screenshot_and_no_vision(self):
        http = Http([answer("", "ok", True)])
        code, out, records = run_main(self.game, ["go", "--screenshot-cmd", shell(sys.executable, "-c", "pass")],
                                      {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0, out)
        self.assertIn("vision on, but the first screenshot failed", out)
        user = http.requests[0][3]["messages"][1]["content"]
        self.assertIn("(no screenshot this turn: the screenshot command failed (exit 0))", user)
        for args, why in ((["--no-vision"], "vision off (--no-vision)"),
                          (["--model", "pro"], "vision off (deepseek-v4-pro does not take images)")):
            http = Http([answer("", "ok", True)])
            code, out, _ = run_main(self.game, ["go", "--screenshot-cmd", "false"] + args, {"DEEPSEEK_API_KEY": KEY},
                                    http)
            self.assertEqual(code, 0, out)
            self.assertIn(why, out)
            self.assertIsInstance(http.requests[0][3]["messages"][1]["content"], str)
            self.assertNotIn("Screenshot:", http.requests[0][3]["messages"][0]["content"])

    def test_guide_option_and_default_limit(self):
        self.assertEqual(p.parse_args(["go"]).max_usd, 0.5)
        self.assertEqual(p.parse_args(["go", "--guide", "none"]).guide, "")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "guide.md")
            with open(path, "w") as f:
                f.write("Kris is the one you play.")
            http = Http([answer("", "ok", True)])
            code, _, _ = run_main(self.game, ["go", "--guide", path], {"DEEPSEEK_API_KEY": KEY}, http)
            self.assertEqual(code, 0)
            system = http.requests[0][3]["messages"][0]["content"]
            self.assertIn("About the game:\n\nKris is the one you play.", system)
            self.assertNotIn("## Save points", system)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                p.parse_args(["go", "--guide", os.path.join(tmp, "missing.md")])

    def test_until_forms(self):
        self.assertEqual(p.parse_until("save_menu"), ("save_menu", p.PRESENT))
        self.assertEqual(p.parse_until("save_menu=null"), ("save_menu", None))
        self.assertTrue(p.met({"save_menu": {"stage": 0}}, p.parse_until("save_menu")))
        self.assertFalse(p.met({"save_menu": {"stage": 0}}, p.parse_until("save_menu=null")))
        self.assertTrue(p.met({"save_menu": None}, p.parse_until("save_menu=null")))
        self.assertEqual(p.parse_until("interact=5"), ("interact", 5))
        self.assertEqual(p.parse_until("room=room_town"), ("room", "room_town"))
        self.assertTrue(p.met({"interact": 5.0}, ("interact", 5)))
        self.assertFalse(p.met({"save_menu": None}, ("save_menu", p.PRESENT)))
        self.assertTrue(p.met({"can_move": True}, ("can_move", p.PRESENT)))
        with self.assertRaises(ValueError):
            p.parse_until("=5")

    def test_notes_between_two_players(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "notes.jsonl")
            other = p.Notes(path)
            other.write(2, 5, {"room": "room_test", "player": 2, "players_pos": [{"player": 2, "x": 7, "y": 8}]},
                        "look on the left", "I take the left side")
            http = Http([answer("10 right", "walk", note="I go right"), answer("", "ok", True)])
            code, out, records = run_main(self.game, ["go", "--notes", path], {"DEEPSEEK_API_KEY": KEY}, http)
            self.assertEqual(code, 0, out)
            self.assertIn("notes %s" % path, out)
            first = http.requests[0][3]["messages"]
            self.assertIn('Other players: the other players of this game are played by programs', first[0]["content"])
            self.assertIn('"note": "<at most 25 words>"}', first[0]["content"])
            self.assertIn('Other players (from the notes you share):\nplayer 2, 0 s ago (its turn 5), in room_test '
                          'at x=7 y=8, doing: "look on the left"\n  player 2 wrote 0 s ago, in room_test: '
                          '"I take the left side"', first[1]["content"])
            self.assertEqual(http.requests[1][3]["messages"][0], first[0])     # the system prompt stays cached
            self.assertIn("(your note to the other players) I go right", http.requests[1][3]["messages"][1]["content"])
            with open(path, encoding="utf-8") as f:
                mine = [json.loads(line) for line in f][1:]
            self.assertEqual([(r["player"], r["turn"], r["doing"], r["note"], r["x"]) for r in mine],
                             [(1, 1, "walk", "I go right", 0), (1, 2, "ok", "", 30)])
            self.assertEqual(records[1]["to_others"], "I go right")
        http = Http([answer("", "ok", True)])
        code, _, _ = run_main(self.game, ["go"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0)
        self.assertNotIn("you share notes with them", http.requests[0][3]["messages"][0]["content"])
        self.assertNotIn("Other players", http.requests[0][3]["messages"][1]["content"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = p.main(["go", "--port", str(self.game.port), "--no-vision", "--notes",
                           os.path.join(tmp, "gone", "notes.jsonl")], {"DEEPSEEK_API_KEY": KEY}, io.StringIO(),
                          Http([]))
        self.assertEqual(code, 2)
        self.assertIn("error: --notes:", err.getvalue())

    def test_where_the_room_changed_is_remembered(self):
        self.game.door = 45
        http = Http([answer("20 right", "walk to the door"), answer("", "ok", True)])
        code, out, _ = run_main(self.game, ["go"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0, out)
        self.assertIn("exits:\n  no door object: you came in here from room_test (+0,+0) here",
                      http.requests[1][3]["messages"][1]["content"])

    def test_back_and_forth_is_told_to_the_model(self):
        http = Http([answer("10 right"), answer("10 left")] * 4 + [answer("", "ok", True)])
        code, out, records = run_main(self.game, ["go"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0, out)
        users = [r[3]["messages"][1]["content"] for r in http.requests]
        self.assertFalse(any("Warning from the program" in u for u in users[:7]))
        self.assertIn("Warning from the program: in your last 8 turns you walked back and forth between x=0 and "
                      "x=30 (y about 0)", users[8])
        self.assertIn("back and forth", records[-2]["swing"])

    def test_measured_speed_goes_into_the_prompt(self):
        http = Http([answer("20 right"), answer("", "ok", True)])
        code, _, _ = run_main(self.game, ["go"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0)
        self.assertNotIn("speed measured", http.requests[0][3]["messages"][1]["content"])
        self.assertIn("speed measured in this run: about 3.0 px", http.requests[1][3]["messages"][1]["content"])

    def test_pieces(self):
        frames = a_frames("10 right; tap confirm 3 2; 5 right+cancel")
        self.assertEqual([len(x) for x in p.pieces(frames)], [10, 3, 3, 3, 5])
        self.assertEqual(p.pieces([]), [])

    def test_deepseek_request_and_the_key_stays_out_of_the_log(self):
        http = Http([answer("30 right", "walk"), answer("", "there", True)])
        code, out, records = run_main(self.game, ["walk to the save point"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0, out)
        method, url, headers, body = http.requests[0]
        self.assertEqual((method, url), ("POST", "https://api.deepseek.com/chat/completions"))
        self.assertEqual(headers["Authorization"], "Bearer " + KEY)
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["max_tokens"], 300)
        self.assertEqual(http.requests[0][3]["messages"][0], http.requests[1][3]["messages"][0])
        self.assertNotIn(KEY, out)
        self.assertNotIn(KEY, json.dumps(records))
        self.assertAlmostEqual(records[-1]["usd_total"], 2 * (640 * 0.006 + 260 * 0.3 + 40 * 1.2) / 1e6, places=6)

    def test_refused_key(self):
        http = Http([(401, {"error": {"message": "Authentication Fails, your api key: %s is invalid" % KEY}})])
        code, out, _ = run_main(self.game, ["go"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 1)
        self.assertIn("DeepSeek", out)
        self.assertNotIn(KEY, out)

    def test_no_key(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), tempfile.TemporaryDirectory() as tmp:
            code = p.main(["go", "--port", str(self.game.port), "--key-file", os.path.join(tmp, "none")], {})
        self.assertEqual(code, 2)
        self.assertIn("DEEPSEEK_API_KEY", err.getvalue())

    def test_step_limit(self):
        code, out, records = run_main(self.game, ["go", "--max-steps", "3"], {"DEEPSEEK_API_KEY": KEY},
                                      Http([answer("wait 1")] * 3))
        self.assertEqual(code, 3)
        self.assertIn("step limit (3)", out)
        self.assertEqual(len([r for r in records if r["event"] == "turn"]), 3)

    def test_spending_limit_stops_before_the_call(self):
        http = Http([answer("wait 1")] * 10)
        code, out, records = run_main(self.game, ["go", "--max-usd", "0.0035"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 3)
        self.assertIn("spending limit", out)
        self.assertLessEqual(records[-1]["usd_total"], 0.0035)
        self.assertGreater(len(http.requests), 0)

    def test_unusable_answers_and_giving_up(self):
        code, out, records = run_main(self.game, ["go"], {"DEEPSEEK_API_KEY": KEY},
                                      Http(["no idea", answer("20 rihgt"), "[]"]))
        self.assertEqual(code, 1)
        self.assertIn("3 unusable answers", out)
        code, out, _ = run_main(self.game, ["go"], {"DEEPSEEK_API_KEY": KEY},
                                Http([answer("", "give up: no save point here", True)]))
        self.assertEqual(code, 1)
        self.assertIn("gave up", out)

    def test_unusable_answer_is_told_to_the_model(self):
        http = Http(["walk right", answer("", "ok", True)])
        code, _, _ = run_main(self.game, ["go"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0)
        self.assertIn("your answer was unusable", http.requests[1][3]["messages"][1]["content"])

    def test_long_answer_is_cut(self):
        code, _, records = run_main(self.game, ["go", "--max-frames", "30"], {"DEEPSEEK_API_KEY": KEY},
                                    Http([answer("100 right"), answer("", "ok", True)]))
        self.assertEqual(code, 0)
        self.assertEqual(self.game.x, 90)
        self.assertEqual(records[1]["frames"], 30)
        self.assertIn("cut to 30 frames", records[1]["note"])

    def test_answer_without_a_message(self):
        for body in ({"choices": [None]}, {"choices": []}, {"choices": [{"message": {"content": None}}]}):
            code, out, _ = run_main(self.game, ["go"], {"DEEPSEEK_API_KEY": KEY}, Http([(200, body)]))
            self.assertEqual(code, 1, body)
            self.assertIn("without a message", out)

    def test_time_limit_around_the_call(self):
        now = [0.0]
        model = p.FakeModel([answer("20 right")])
        slow = model.ask

        def ask(msgs, seconds=120):
            self.assertLessEqual(seconds, 60)
            now[0] += 61                # the answer comes after the time is up
            return slow(msgs, seconds)
        model.ask = ask
        with agent_client.AgentClient(port=self.game.port) as game:
            with self.assertRaises(p.Stop) as cm:
                p.play(game, model, "go", max_minutes=1, clock=lambda: now[0])
        self.assertEqual(cm.exception.code, 3)
        self.assertEqual(self.game.x, 0)            # its steps were not played
        times = iter([0.0] + [40.0] * 10)         # 20 s left: too little for a call
        model = p.FakeModel([answer("20 right")])
        with agent_client.AgentClient(port=self.game.port) as game:
            with self.assertRaises(p.Stop) as cm:
                p.play(game, model, "go", max_minutes=1, clock=lambda: next(times))
        self.assertEqual(cm.exception.code, 3)
        self.assertIn("too little left", cm.exception.why)
        self.assertEqual(model.asked, [])

    def test_time_limit_around_the_state_read_and_the_pause(self):
        now = [0.0]
        with agent_client.AgentClient(port=self.game.port) as game:
            read = game.state
            reads = []

            def state():
                reads.append(1)
                if len(reads) == 2:         # the read after the answer ends after the time is up
                    now[0] += 61
                return read()
            game.state = state
            with self.assertRaises(p.Stop) as cm:
                p.play(game, p.FakeModel([answer("20 right", "go", True)]), "go", max_minutes=1,
                       clock=lambda: now[0])
        self.assertEqual(cm.exception.code, 3)          # "done" is not accepted once the time is up
        self.assertEqual(self.game.x, 0)
        now, slept = [0.0], []

        def sleep(seconds):
            slept.append(seconds)
            now[0] += seconds
        model = p.FakeModel([answer("wait 1"), answer("wait 1")])
        with agent_client.AgentClient(port=self.game.port) as game:
            with self.assertRaises(p.Stop) as cm:
                p.play(game, model, "go", max_minutes=1, pause=1000, clock=lambda: now[0], sleep=sleep)
        self.assertEqual(cm.exception.code, 3)
        self.assertEqual(slept, [60.0])                 # the pause stops at the time limit, not after 1000 s

    def test_dialogue_that_does_not_go_away_goes_to_the_model(self):
        self.game.dialogue = ["* Stuck.", 0]
        self.game.apply = lambda bits: None     # confirm changes nothing
        http = Http([answer("", "stuck in a box", True)])
        code, _, records = run_main(self.game, ["go"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0)
        turns = [r for r in records if r["event"] == "turn"]
        self.assertEqual([r.get("auto", False) for r in turns], [True, True, False])

    def test_typing_box_is_waited_for(self):
        self.game.x = 90
        self.game.apply(16)
        self.game.apply(0)
        http = Http([answer("", "menu open", True)])
        code, _, records = run_main(self.game, ["go"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0)
        self.assertEqual([r["do"] for r in records if r["event"] == "turn"],
                         ["wait 20", "tap confirm; wait 4", ""])
        self.assertEqual(self.game.menu["stage"], 0)

    def test_prompt_only(self):
        out = io.StringIO()
        code = p.main(["walk to the save point", "--port", str(self.game.port), "--prompt", "--no-vision"], {}, out,
                      Http([]))
        self.assertEqual(code, 0)
        self.assertIn("Goal: walk to the save point", out.getvalue())
        self.assertIn("nearby: obj_savepoint (save point) (+90,-10)", out.getvalue())

    def test_no_game(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        nobody = type("Nobody", (), {"port": s.getsockname()[1]})
        s.close()
        code, out, _ = run_main(nobody, ["go"], {"DEEPSEEK_API_KEY": KEY})
        self.assertEqual(code, 1)
        self.assertIn("cannot connect", out)


if __name__ == "__main__":
    unittest.main()

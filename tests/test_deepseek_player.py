"""tools/deepseek_player.py without the game and without DeepSeek: a small game in Python that speaks the Agent API
on a local port (a player who walks, a save point with a dialogue and a choice), a fake model, and a fake HTTP
layer in place of api.deepseek.com. The player against the real drnet.dll is part of `native/test_agent.py solo`."""
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import agent_client  # noqa: E402
import deepseek_player as p  # noqa: E402

KEY = "sk-test0123456789abcdef"


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


def answer(do="", why="", done=False):
    return json.dumps({"why": why, "do": do, "done": done})


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
        self.assertIn("nearby: obj_savepoint (+20,-10) solid", text)

    def test_summary_of_the_stand_in_game_and_of_nulls(self):
        s = {"frame": 3, "mode": "solo", "player": 1, "players_pos": [{"player": 1, "x": 30, "y": 0, "confirm": 2}]}
        self.assertEqual(p.summarize(s), "you at x=30 y=0")
        s = {"room": None, "chapter": 2, "dialogue": None, "choice": None, "nearby": None, "players_pos": None,
             "desync": -1}
        self.assertEqual(p.summarize(s), "chapter=2")

    def test_save_menu(self):
        s = {"save_menu": {"stage": 0, "x": 0, "y": 0, "slot": 0, "overwrite": 0, "overwrite_x": 0}}
        self.assertIn('save point menu (open): {"stage":0,"x":0,"y":0,', p.summarize(s))
        self.assertNotIn("save point menu", p.summarize({"save_menu": None, "room": "r"}))

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
        self.assertIn("150 frames", a[0]["content"])
        self.assertIn("turn 1: 20 right", b[1]["content"])

    def test_change(self):
        a = {"room": "r", "player": 1, "players_pos": [{"player": 1, "x": 0, "y": 0}], "can_move": True}
        b = {"room": "r", "player": 1, "players_pos": [{"player": 1, "x": 30, "y": -6}], "can_move": False,
             "choice": {"options": []}}
        self.assertEqual(p.change(a, a), "nothing changed")
        self.assertEqual(p.change(a, b), "moved (+30,-6), can_move True -> False, choice appeared")


class Answers(unittest.TestCase):
    def test_plain_json(self):
        why, do, frames, done, asked = p.parse_answer(answer("20 right; tap z", "walk"), 150)
        self.assertEqual((why, do, done, asked), ("walk", "20 right; tap z", False, 22))
        self.assertEqual(len(frames), 22)

    def test_json_inside_other_words(self):
        why, do, frames, done, _ = p.parse_answer('Sure:\n```json\n{"why":"ok","do":"5 up","done":true}\n```', 150)
        self.assertEqual((do, done, len(frames)), ("5 up", True, 5))

    def test_frames_are_cut(self):
        self.assertEqual(len(p.parse_answer(answer("10s right"), 150)[2]), 150)
        self.assertEqual(p.parse_answer(answer("10s right"), 150)[4], 300)

    def test_huge_answers_are_refused_before_they_are_built(self):
        for do in ("999999999 right", "wait 99999999", "tap z 100000 100000", "1 right; 2000000s left"):
            with self.assertRaises(ValueError, msg=do) as cm:
                p.parse_answer(answer(do), 150)
            self.assertIn("send at most 150", str(cm.exception))
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
            code = p.main(["--port", str(game.port), "--pause", "0", "--log", log,
                           "--key-file", os.path.join(tmp, "none")] + args, env or {}, out, http or Http([]))
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
        self.assertIn("ends the run by itself as soon as the state has save_menu",
                      http.requests[0][3]["messages"][0]["content"])

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
        code, out, records = run_main(self.game, ["go", "--max-usd", "0.002"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 3)
        self.assertIn("spending limit", out)
        self.assertLessEqual(records[-1]["usd_total"], 0.002)
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
        code, _, records = run_main(self.game, ["go", "--until", "interact=7"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0)
        self.assertEqual([r["do"] for r in records if r["event"] == "turn"],
                         ["wait 20", "tap confirm; wait 4", ""])
        self.assertEqual(self.game.menu["stage"], 0)

    def test_prompt_only(self):
        out = io.StringIO()
        code = p.main(["walk to the save point", "--port", str(self.game.port), "--prompt"], {}, out, Http([]))
        self.assertEqual(code, 0)
        self.assertIn("Goal: walk to the save point", out.getvalue())
        self.assertIn("nearby: obj_savepoint (+90,-10)", out.getvalue())

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

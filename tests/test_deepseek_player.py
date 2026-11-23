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
    """The Agent API on 127.0.0.1, one connection at a time, with a tiny world: right/left move the player 3 px a
    frame; confirm next to the save point at x=90 opens a dialogue, confirm again a choice (Save / Return), left
    picks Save, then confirm saves and shows a last box."""

    SAVE_X = 90

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.x, self.y, self.frame = 0, 0, 0
        self.dialogue = None        # text of the box that waits for confirm
        self.choice = None          # selected option, -1 none
        self.saved = False
        self.last = 0
        self.inputs = []            # every input command's steps, as the game got them
        self.cleared = 0
        threading.Thread(target=self.serve, daemon=True).start()

    def state(self):
        s = {"frame": self.frame, "chapter": 1, "room": "room_test", "player": 1, "battle": False, "menuno": -1,
             "interact": 0 if self.dialogue is None and self.choice is None else 1,
             "players_pos": [{"player": 1, "object": "obj_mainchara", "name": "kris", "x": self.x, "y": self.y,
                              "facing": 1}],
             "dialogue": [] if self.dialogue is None else [{"text": self.dialogue, "waiting": True}],
             "choice": None if self.choice is None else {"options": ["Save", "Return"], "selected": self.choice,
                                                         "can_choose": True},
             "nearby": [{"object": "obj_savepoint", "x": self.SAVE_X, "y": -10, "solid": True, "visible": True}]}
        s["can_move"] = s["interact"] == 0
        return s

    def apply(self, bits):
        pressed = bits & ~self.last
        self.last = bits
        self.frame += 1
        confirm = pressed & 16
        if self.choice is not None:
            if bits & 8:
                self.choice = 0
            if confirm and self.choice == 0:
                self.choice, self.saved, self.dialogue = None, True, "The game was saved."
            return
        if self.dialogue is not None:
            if confirm:
                self.dialogue = None
                if not self.saved:
                    self.choice = -1
            return
        self.x += 3 * bool(bits & 2) - 3 * bool(bits & 8)
        if confirm and abs(self.x - self.SAVE_X) <= 20:
            self.dialogue = "* A save point. & * Determination."

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


def answer(do="", why="", done=False):
    return json.dumps({"why": why, "do": do, "done": done})


class Prompt(unittest.TestCase):
    def test_summary_of_a_full_state(self):
        g = FakeGame()
        self.addCleanup(g.close)
        g.x, g.dialogue = 70, "* Hello &  there."
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
        why, do, frames, done = p.parse_answer(answer("20 right; tap z", "walk"), 150)
        self.assertEqual((why, do, done), ("walk", "20 right; tap z", False))
        self.assertEqual(len(frames), 22)

    def test_json_inside_other_words(self):
        why, do, frames, done = p.parse_answer('Sure:\n```json\n{"why":"ok","do":"5 up","done":true}\n```', 150)
        self.assertEqual((do, done, len(frames)), ("5 up", True, 5))

    def test_frames_are_cut(self):
        self.assertEqual(len(p.parse_answer(answer("10s right"), 150)[2]), 150)

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

    def test_save_with_the_fake_model(self):
        answers = [answer("30 right", "walk to the save point"), answer("tap confirm", "use it"),
                   answer("tap left; tap confirm", "pick Save"), answer("", "saved", True)]
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "answers.txt")
            with open(fake, "w") as f:
                f.write("\n".join(answers) + "\n")
            code, out, records = run_main(self.game, ["walk to the save point and save", "--fake", fake])
        self.assertEqual(code, 0, out)
        self.assertTrue(self.game.saved)
        self.assertEqual(self.game.x, 90)
        turns = [r for r in records if r["event"] == "turn"]
        self.assertEqual([r.get("auto", False) for r in turns], [False, False, True, False, True, False])
        self.assertEqual(turns[0]["do"], "30 right")
        self.assertEqual(turns[0]["result"], "moved (+90,+0)")
        self.assertIn("choice appeared", turns[2]["result"])
        self.assertEqual(records[-1]["event"], "end")
        self.assertIn("goal is reached: saved", out)
        self.assertEqual(self.game.cleared, 1)          # the queued input is dropped at the end

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
        code, out, records = run_main(self.game, ["go", "--max-usd", "0.0008"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 3)
        self.assertIn("spending limit", out)
        self.assertLessEqual(records[-1]["usd_total"], 0.0008)
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
        code, _, records = run_main(self.game, ["go", "--max-frames", "10"], {"DEEPSEEK_API_KEY": KEY},
                                    Http([answer("100 right"), answer("", "ok", True)]))
        self.assertEqual(code, 0)
        self.assertEqual(self.game.x, 30)
        self.assertEqual(records[1]["frames"], 10)

    def test_dialogue_that_does_not_go_away_goes_to_the_model(self):
        self.game.SAVE_X = 0
        self.game.apply(16)         # opens the box at x=0
        self.game.apply(0)
        self.game.apply = lambda bits: None     # from now on confirm changes nothing
        http = Http([answer("", "stuck in a box", True)])
        code, _, records = run_main(self.game, ["go"], {"DEEPSEEK_API_KEY": KEY}, http)
        self.assertEqual(code, 0)
        turns = [r for r in records if r["event"] == "turn"]
        self.assertEqual([r.get("auto", False) for r in turns], [True, False])

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

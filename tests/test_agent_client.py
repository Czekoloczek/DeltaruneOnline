"""tools/agent_client.py without a game or a network: steps from text, the limits of the game side, lines cut from
the bytes of a socket, answers matched to commands, the command line. The client against the real drnet.dll is
native/test_agent.py."""
import contextlib
import io
import json
import os
import re
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import agent_client as a  # noqa: E402
import input_script  # noqa: E402


def step(frames, *buttons):
    return {"buttons": list(buttons), "frames": frames}


class Steps(unittest.TestCase):
    def test_hold_wait_tap(self):
        self.assertEqual(a.text_to_steps("20 right"), [step(20, "right")])
        self.assertEqual(a.text_to_steps("wait 5"), [step(5)])
        self.assertEqual(a.text_to_steps("tap confirm 2 15"),
                         [step(1, "confirm"), step(15), step(1, "confirm"), step(15)])
        self.assertEqual(a.text_to_steps("1 up+z+r"), [step(1, "right", "up", "confirm")])

    def test_semicolons_lines_and_comments(self):
        self.assertEqual(a.text_to_steps("20 right; 5 right"), [step(25, "right")])
        self.assertEqual(a.text_to_steps("2 right;; 1 up ;"), [step(2, "right"), step(1, "up")])
        self.assertEqual(a.text_to_steps("2 right  # walk; 9 left\n1 up\n\n# nothing; 3 down\n"),
                         [step(2, "right"), step(1, "up")])
        self.assertEqual(a.text_to_steps(""), [])

    def test_same_frames_as_input_script(self):
        text = "wait 3\n2 right\ntap z 2 3\n1 up+cancel"
        self.assertEqual(a.text_to_frames(text.replace("\n", "; ")), input_script.compile_text(text))

    def test_long_hold_is_split(self):
        self.assertEqual(a.text_to_steps("5s right; 7300 up; 1 up"),
                         [step(150, "right"), step(3600, "up"), step(3600, "up"), step(101, "up")])

    def test_only_the_seven_buttons(self):
        self.assertEqual(a.AGENT_BUTTONS, ["down", "right", "up", "left", "confirm", "cancel", "menu"])
        for text in ("20 teleport", "1 right+f2"):
            with self.assertRaises(ValueError) as cm:
                a.text_to_steps(text)
            self.assertIn("only the buttons", str(cm.exception))

    def test_errors_name_the_step(self):
        with self.assertRaises(input_script.ScriptError) as cm:
            a.text_to_steps("20 right; 5 rihgt")
        self.assertIn("step 2 (5 rihgt)", str(cm.exception))
        self.assertNotIn("line 1", str(cm.exception))

    def test_chunks(self):
        steps = a.text_to_steps("; ".join(["1 right", "1 left"] * 65))
        chunks = a.chunk_steps(steps)
        self.assertEqual([len(c) for c in chunks], [60, 60, 10])
        self.assertEqual(sum(chunks, []), steps)
        self.assertEqual(a.chunk_steps([]), [])

    def test_check_steps(self):
        self.assertEqual(a.check_steps([step(3, "up"), step(3600)]), 3603)
        for bad in ([step(0)], [step(3601)], [step(1.5)], [step(True)], [step(1, "f1")], [{"frames": 1}], ["20 right"],
                    [{"buttons": "right", "frames": 1}]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                a.check_steps(bad)


class Limits(unittest.TestCase):
    def test_same_limits_and_buttons_as_the_dll(self):
        with open(os.path.join(ROOT, "native", "drnet.c"), encoding="utf-8") as f:
            c = f.read()
        for name, value in (("STEPS", a.MAX_STEPS), ("FRAMES", a.MAX_FRAMES), ("INPUTS", a.MAX_QUEUED)):
            self.assertEqual(int(re.search(r"#define AGENT_MAX_%s (\d+)" % name, c).group(1)), value, name)
        names = re.search(r"agent_buttons\[7\] = \{([^}]*)\}", c).group(1)
        self.assertEqual(re.findall(r'"(\w+)"', names), a.AGENT_BUTTONS)


class Lines(unittest.TestCase):
    def test_encode(self):
        self.assertEqual(a.encode_line({"cmd": "state", "id": 7}), b'{"cmd":"state","id":7}\n')
        line = a.encode_line({"id": "zażółć\n "})
        self.assertEqual(line.count(b"\n"), 1)
        self.assertTrue(all(b < 128 for b in line))
        self.assertEqual(a.decode_line(line), {"id": "zażółć\n "})

    def test_reader_any_pieces(self):
        data = ('{"type":"state","id":1,"state":{"room":"zażółć"}}\r\n\n{"type":"done","id":2}\n{"type":"err').encode("utf-8")
        want = [{"type": "state", "id": 1, "state": {"room": "zażółć"}}, {"type": "done", "id": 2}]
        for size in (1, 2, 3, 7, len(data)):
            r = a.LineReader()
            lines = []
            for i in range(0, len(data), size):
                lines += r.feed(data[i:i + size])
            self.assertEqual([a.decode_line(x) for x in lines], want, size)
            self.assertEqual(r.buf, b'{"type":"err')

    def test_broken_utf8_does_not_raise(self):
        self.assertEqual(a.decode_line(b'{"a":"\xff"}'), {"a": "�"})


class FakeGame:
    """Stands in for the socket: answers every command line from a script of replies."""

    def __init__(self, answer, piece=5):
        self.answer, self.piece = answer, piece
        self.sent, self.out = [], b""

    def setsockopt(self, *args):
        pass

    def settimeout(self, t):
        pass

    def sendall(self, data):
        for line in data.splitlines():
            cmd = json.loads(line)
            self.sent.append(cmd)
            for reply in self.answer(cmd):
                self.out += json.dumps(reply).encode() + b"\n"

    def recv(self, n):
        data, self.out = self.out[:self.piece], self.out[self.piece:]
        return data

    def close(self):
        pass


def client(answer):
    c = a.AgentClient(timeout=0.2)
    c.sock = FakeGame(answer)
    return c


class Client(unittest.TestCase):
    def test_state_and_clear(self):
        c = client(lambda cmd: [{"type": "state" if cmd["cmd"] == "state" else "cleared", "id": cmd["id"], "state": {"frame": 3}}])
        self.assertEqual(c.state(), {"frame": 3})
        self.assertIsNone(c.clear())
        self.assertEqual([m["cmd"] for m in c.sock.sent], ["state", "clear"])
        self.assertNotEqual(c.sock.sent[0]["id"], c.sock.sent[1]["id"])

    def test_error_for_the_command(self):
        c = client(lambda cmd: [{"type": "error", "id": cmd["id"], "error": "unknown cmd"}])
        with self.assertRaisesRegex(a.AgentError, "unknown cmd"):
            c.state()

    def test_error_without_id_and_closed_connection(self):
        c = client(lambda cmd: [{"type": "error", "id": None, "error": "another agent is connected"}])
        with self.assertRaisesRegex(a.AgentError, "another agent"):
            c.state()
        c = client(lambda cmd: [])
        with self.assertRaisesRegex(a.AgentError, "closed the connection"):
            c.state()

    def test_other_answers_are_kept(self):
        # the answer to an older command comes first: it must not be taken for this one, nor lost
        c = client(lambda cmd: [{"type": "done", "id": 99, "state": {}}, {"type": "state", "id": cmd["id"], "state": {"x": 1}}])
        self.assertEqual(c.state(), {"x": 1})
        self.assertEqual(c._wait("done", 99, 0.1)["type"], "done")

    def test_long_input_is_sent_in_commands_without_overfilling_the_queue(self):
        queue, most = [], [0]

        def game(cmd):
            # like the game: "queued" at once; a command is done when the next one arrives or nothing else is left
            self.assertEqual(cmd["cmd"], "input")
            self.assertLessEqual(len(cmd["steps"]), a.MAX_STEPS)
            queue.append(cmd["id"])
            most[0] = max(most[0], len(queue))
            out = [{"type": "queued", "id": cmd["id"], "frames": sum(s["frames"] for s in cmd["steps"])}]
            if len(queue) >= a.MAX_QUEUED or len(c.sock.sent) == total:
                out += [{"type": "done", "id": queue.pop(0), "state": {"last": cmd["id"]}}]
                if len(c.sock.sent) == total:
                    out += [{"type": "done", "id": i, "state": {"last": i}} for i in queue]
            return out

        steps = a.text_to_steps("; ".join(["1 right", "1 up"] * 400))
        total = len(a.chunk_steps(steps))
        self.assertEqual(total, 14)
        c = client(game)
        state = c.send_steps(steps)
        self.assertEqual(len(c.sock.sent), total)
        self.assertEqual(sum((m["steps"] for m in c.sock.sent), []), steps)
        self.assertEqual(most[0], a.MAX_QUEUED)
        self.assertEqual(state, {"last": c.sock.sent[-1]["id"]})

    def test_refused_steps_are_not_sent(self):
        c = client(lambda cmd: [])
        with self.assertRaises(ValueError):
            c.send_steps([step(2, "right"), step(0, "up")])
        self.assertEqual(c.sock.sent, [])


class CommandLine(unittest.TestCase):
    def test_options_before_and_after_the_command(self):
        for argv in (["--port", "9000", "state"], ["state", "--port", "9000"], ["state", "--port=9000"]):
            args = a.parse_args(argv)
            self.assertEqual((args.command, args.host, args.port, args.timeout), ("state", "127.0.0.1", 9000, 10.0), argv)
        args = a.parse_args(["--port", "9000", "--timeout", "3", "do", "20 right", "--host", "localhost"])
        self.assertEqual((args.command, args.steps, args.host, args.port, args.timeout), ("do", "20 right", "localhost", 9000, 3.0))
        args = a.parse_args(["clear"])
        self.assertEqual((args.command, args.port), ("clear", a.DEFAULT_PORT))

    def run_main(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return a.main(argv), err.getvalue()

    def test_bad_steps_exit_2_without_connecting(self):
        code, err = self.run_main(["do", "20 teleport", "--port", "1"])
        self.assertEqual(code, 2)
        self.assertIn("only the buttons", err)

    def test_no_game_exits_1(self):
        code, err = self.run_main(["state", "--port", "1", "--timeout", "0.5"])
        self.assertEqual(code, 1)
        self.assertIn("cannot connect to the game on 127.0.0.1:1", err)

    def test_state_for_any_terminal(self):
        state = {"room": "zażółć"}
        self.assertIn("zażółć", a.format_state(state, "utf-8"))
        self.assertEqual(json.loads(a.format_state(state, "ascii")), state)
        self.assertTrue(a.format_state(state, None).isascii())


if __name__ == "__main__":
    unittest.main()

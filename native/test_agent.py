#!/usr/bin/env python3
"""The Agent API of drnet.dll without the game: a stand-in game (native/build/test_agent.exe) loads the DLL and
calls it the way gml/scr_dronline.gml does, and this script talks to it as an agent would, through
tools/agent_client.py and through plain sockets.

  python3 native/test_agent.py [solo] [long] [online]      default: all three

  solo    one game: state, input and its exact effect, ids, every kind of refused command, the limits, clear,
          a second connection, input that ends with its connection, a browser's HTTP request, the command line,
          the port closed and opened again as at a chapter change
  long    a game whose state is 300 KB: long replies arrive whole, and an agent that stops reading is dropped
  online  host and client in lockstep, an agent on each: "done" comes only when the delayed input was applied,
          and both sides see the same; once with a fixed delay, once with an automatic delay that grows and
          shrinks while input is played (no press may be lost or doubled)

On Linux the game runs under Wine (WINE=path picks another Wine, e.g. Proton's files/bin/wine); on Windows it
runs natively. Needs native/build/drnet.dll and native/build/test_agent.exe (sh native/build.sh). The GML that
builds the real state is not part of this: it needs the game. Used by tools/verify.sh and
.github/workflows/tests.yml.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BUILD = os.path.join(ROOT, "native", "build")
CLIENT = os.path.join(ROOT, "tools", "agent_client.py")
sys.path.insert(0, os.path.join(ROOT, "tools"))
from agent_client import AgentClient, AgentError, LineReader, decode_line, encode_line  # noqa: E402

ALL = ["down", "right", "up", "left", "confirm", "cancel", "menu"]
games = []      # every game started, to stop what is left at the end


def check(ok, what, *details):
    if not ok:
        raise AssertionError(what + "".join("\n    " + repr(d) for d in details))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def runner():
    """The words that start a Windows program here, and the environment for it."""
    env = dict(os.environ)
    if os.name == "nt":
        return [], env
    env.update(WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree,mshtml=")    # no Mono/Gecko install prompts
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    env.setdefault("WINEPREFIX", os.path.join(cache, "dronline-test", "pair"))      # the prefix of test_pair.sh
    os.makedirs(os.path.dirname(env["WINEPREFIX"]), exist_ok=True)      # Wine creates the prefix, but not its parent
    env.pop("TMPDIR", None)     # the Wine 9.0 of Ubuntu 24.04 aborts at start when TMPDIR is set
    return [os.environ.get("WINE") or "wine"], env


class Game:
    """test_agent.exe, under Wine or natively."""

    def __init__(self, tmp, mode, port, fps=100, pad=0, delay=-1, lag=0, wave=0):
        cmd, env = runner()
        cmd = cmd + [os.path.join(BUILD, "test_agent.exe")]
        self.name = "%s game" % mode
        self.log = os.path.join(tmp, "%s-%d.txt" % (mode, port))
        self.out = open(self.log, "wb")
        self.proc = subprocess.Popen(cmd + [mode, str(port), str(fps), str(pad), str(delay), str(lag), str(wave)],
                                     cwd=BUILD, env=env, stdin=subprocess.DEVNULL, stdout=self.out, stderr=subprocess.STDOUT)
        games.append(self)

    def output(self):
        with open(self.log, "rb") as f:
            return f.read().decode("utf-8", errors="replace").strip()

    def wait_exit(self, seconds=20):
        try:
            code = self.proc.wait(seconds)
        except subprocess.TimeoutExpired:
            raise AssertionError("the %s did not end" % self.name)
        check(code == 0 and " done: " in self.output(), "the %s ended badly (exit code %d)" % (self.name, code), self.output())

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(10)
        self.out.close()


def connect(game, port, seconds=120, timeout=20):
    """An AgentClient, once the game listens. The first start of Wine creates its prefix, which takes a while."""
    end = time.monotonic() + seconds
    while True:
        check(game.proc.poll() is None, "the %s ended at once" % game.name, game.output())
        try:
            return AgentClient(port=port, timeout=timeout).connect()
        except AgentError:
            check(time.monotonic() < end, "the %s does not listen on port %d" % (game.name, port), game.output())
            time.sleep(0.2)


class Raw:
    """A plain connection: sends any bytes, reads the replies line by line."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.reader, self.lines = LineReader(), []

    def send(self, *msgs):
        """Dicts go out as JSON lines, bytes as they are plus a line end; all in one piece."""
        self.sock.sendall(b"".join(encode_line(m) if isinstance(m, dict) else m + b"\n" for m in msgs))

    def read(self, seconds=20):
        """The next reply, or None when the game has closed the connection (or nothing came in time: 'silent')."""
        end = time.monotonic() + seconds
        while not self.lines:
            left = end - time.monotonic()
            if left <= 0:
                return "silent"
            self.sock.settimeout(left)
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                return "silent"
            except OSError:
                return None
            if not data:
                return None
            self.lines += self.reader.feed(data)
        return decode_line(self.lines.pop(0))

    def ask(self, msg, kind):
        self.send(msg)
        reply = self.read()
        check(isinstance(reply, dict) and reply.get("type") == kind, "expected a %r reply" % kind, msg, reply)
        return reply

    def state(self):
        return self.ask({"cmd": "state"}, "state")["state"]

    def close(self):
        self.sock.close()


def pos(state, player=1):
    return [p for p in state["players_pos"] if p["player"] == player][0]


def hold(frames, *buttons):
    return {"buttons": list(buttons), "frames": frames}


def still(agent, seconds=0.4):
    """The state, after checking that nothing moves any more: no input is being played."""
    a = agent.state()
    time.sleep(seconds)
    b = agent.state()
    check(b["frame"] > a["frame"], "the game does not run", a["frame"], b["frame"])
    check(a["players_pos"] == b["players_pos"], "input is still being played", a["players_pos"], b["players_pos"])
    return b


# ---- solo ----

def test_basics(port, game):
    s0 = game.state()
    check(s0["mode"] == "solo" and s0["player"] == 1 and pos(s0)["x"] == 0, "the first state", s0)
    s1 = game.do("30 right")
    check(pos(s1) == dict(pos(s0), x=30), "30 right must end at x 30, seen in the state of 'done'", pos(s1))
    check(s1["frame"] >= s0["frame"] + 30, "30 frames must have passed", s0["frame"], s1["frame"])
    s2 = game.do("tap confirm 3 2; 10 up+left+cancel; wait 5; 4 menu")
    check(pos(s2) == {"player": 1, "x": 20, "y": -10, "confirm": 3, "cancel": 1, "menu": 1}, "taps and holds", pos(s2))
    check(pos(still(game)) == pos(s2), "nothing moves after 'done'")
    print("  ok: state, input, and 'done' shows exactly what the input did")


def test_ids_and_order(port):
    r = Raw(port)
    for text in ("a", "", "quote \" backslash \\ slash / tab \t", "zażółć \u2028 \U0001F600", "x" * 40):
        check(r.ask({"cmd": "state", "id": text}, "state")["id"] == text, "a string id comes back unchanged", text)
    for raw, want in ((b"7", 7), (b"-1.5e3", -1500.0), (b"0", 0), (b"null", None), (b"12345678901234567890", 12345678901234567890)):
        r.send(b'{"id":' + raw + b',"cmd":"state"}')
        check(r.read()["id"] == want, "a number id comes back unchanged", raw)
    r.send(b'{"cmd":"state"}')
    check(r.read()["id"] is None, "no id comes back as null")
    r.send(b'  { "more" : {"a":[1,2,{"b":null}],"t":true,"f":false,"n":-0.5E+2} , "id" : 1 , "cmd" : "state" }  \r')
    check(r.read()["type"] == "state", "spaces, a CR line end and keys the game does not know are fine")

    # two inputs sent together play one after the other, and each "done" shows the state right after its last frame
    at = pos(r.state())
    r.send({"cmd": "input", "id": "A", "steps": [hold(10, "right")]},
           {"cmd": "input", "id": "B", "steps": [hold(10, "left"), hold(5, "up")]},
           {"cmd": "state", "id": "S"})
    replies = [r.read() for _ in range(5)]
    check([(m["type"], m["id"]) for m in replies] == [("queued", "A"), ("queued", "B"), ("state", "S"), ("done", "A"), ("done", "B")],
          "replies in the order of the commands", replies)
    check(replies[0]["frames"] == 10 and replies[1]["frames"] == 15, "'queued' tells the frames", replies[:2])
    check(pos(replies[3]["state"]) == dict(at, x=at["x"] + 10), "'done' of the first input", pos(replies[3]["state"]))
    check(pos(replies[4]["state"]) == dict(at, y=at["y"] - 5), "'done' of the second input", pos(replies[4]["state"]))
    forms = (b"3e1", b"30.0", b"30", b"0.3E2", b"30.0000000000000000000000", b"0.000000000000000000003e22")
    r.send(*[b'{"cmd":"input","steps":[{"frames":' + frames + b',"buttons":[]}]}' for frames in forms])
    replies = [r.read() for _ in range(2 * len(forms))]
    check([m["type"] for m in replies] == ["queued"] * len(forms) + ["done"] * len(forms)
          and all(m["frames"] == 30 for m in replies[:len(forms)]), "30 frames written in six ways", replies[:len(forms)])
    r.close()
    print("  ok: ids, reply order, two inputs in a row")


def test_refused(port):
    r = Raw(port)
    at = pos(r.state())
    one = b'{"cmd":"input","id":5,"steps":[%s]}'
    many = b",".join([b'{"buttons":[],"frames":1}'] * 61)
    cases = [
        (b'{"cmd":"jump","id":1}', 1, "unknown cmd"),
        (b'{"cmd":7,"id":1}', 1, "unknown cmd"),
        (b'{"id":"two"}', "two", "no cmd"),
        (b'{}', None, "no cmd"),
        (b'{"cmd":"input","id":3}', 3, "needs steps"),
        (b'{"cmd":"input","id":4,"steps":[]}', 4, "1 to 60 steps"),
        (b'{"cmd":"input","id":4,"steps":[' + many + b']}', 4, "1 to 60 steps"),
        (b'{"cmd":"input","id":4,"steps":{}}', 4, "steps must be a list"),
        (one % b'{"buttons":["right"],"frames":0}', 5, "frames must be"),
        (one % b'{"buttons":["right"],"frames":3601}', 5, "frames must be"),
        (one % b'{"buttons":["right"],"frames":1.5}', 5, "frames must be"),
        (one % b'{"buttons":["right"],"frames":1.0000000000000001}', 5, "frames must be"),
        (one % b'{"buttons":["right"],"frames":3599.99999999999999999999}', 5, "frames must be"),
        (one % b'{"buttons":["right"],"frames":-3}', 5, "frames must be"),
        (one % b'{"buttons":["right"],"frames":1e99}', 5, "frames must be"),
        (one % b'{"buttons":["right"],"frames":"30"}', 5, "frames must be"),
        (one % b'{"buttons":["right"],"frames":null}', 5, "frames must be"),
        (one % b'{"buttons":["right","jump"],"frames":3}', 5, "unknown button"),
        (one % b'{"buttons":["teleport"],"frames":3}', 5, "unknown button"),
        (one % b'{"buttons":"right","frames":3}', 5, "buttons must be"),
        (one % b'{"buttons":[1],"frames":3}', 5, "buttons must be"),
        (one % b'{"frames":3}', 5, "needs buttons and frames"),
        (one % b'{"buttons":["right"]}', 5, "needs buttons and frames"),
        (one % b'{"buttons":["right"],"frames":3},7', 5, "every step must be an object"),
        (b'{"cmd":"state","id":[1]}', None, "id must be"),
        (b'{"cmd":"state","id":true}', None, "id must be"),
        (b'{"cmd":"state","id":"' + b"x" * 200 + b'"}', None, "id is too long"),
        (b'{"cmd":"state"} x', None, "not a JSON object"),
        (b'{"cmd":"state"}{"cmd":"state"}', None, "not a JSON object"),
        (b'{"cmd":"state",}', None, "not a JSON object"),
        (b'{"cmd":"state"', None, "not a JSON object"),
        (b"{'cmd':'state'}", None, "not a JSON object"),
        (b'{"cmd":"state","id":01}', None, "not a JSON object"),
        (b'{"cmd":"state","id":"\xff"}', None, "not a JSON object"),
        (b'{"cmd":"state","id":"\\ud800"}', None, "not a JSON object"),
        (b'{"cmd":"state","id":"a\tb"}', None, "not a JSON object"),
        (b'{"cmd":"state","id":"a\x00b"}', None, "not a JSON object"),
        (b'{"cmd":"state","deep":' + b"[" * 100 + b"]" * 100 + b"}", None, "not a JSON object"),
        (b'["state"]', None, "not a JSON object"),
        (b"state", None, "not a JSON object"),
        (b'{"cmd":"state","id":1' + b" " * 70000 + b"}", None, "longer than 64 KB"),
    ]
    for line, rid, text in cases:
        r.send(line)
        reply = r.read()
        check(isinstance(reply, dict) and reply.get("type") == "error" and reply.get("id") == rid and text in reply.get("error", ""),
              "expected the error %r with id %r" % (text, rid), line[:120], reply)
    r.send(b"", b"   ", b"\r")
    check(pos(still(r)) == at, "refused commands and empty lines do nothing, and the connection goes on")
    r.close()
    print("  ok: %d kinds of bad command are answered with an error, the game goes on" % len(cases))


def test_limits_and_clear(port):
    r = Raw(port)
    at = pos(r.state())
    r.send(*[{"cmd": "input", "id": i, "steps": [hold(3600, "right")] * 60} for i in range(11)])
    replies = [r.read() for _ in range(11)]
    check([m["type"] for m in replies] == ["queued"] * 10 + ["error"] and "queued already" in replies[10]["error"],
          "10 inputs are queued, the 11th is refused", replies)
    check(replies[0]["frames"] == 216000, "60 steps of 3600 frames", replies[0])
    time.sleep(0.3)
    check(r.ask({"cmd": "clear", "id": "c"}, "cleared")["id"] == "c", "clear is answered")
    end = pos(still(r))
    check(at["x"] < end["x"] < at["x"] + 3600, "the input ran until the clear", at, end)
    check(r.read(0.5) == "silent", "a cleared input gets no 'done'")
    check(r.ask({"cmd": "input", "id": "n", "steps": [hold(2, "left")]}, "queued")["frames"] == 2, "the queue is free again")
    check(pos(r.read()["state"])["x"] == end["x"] - 2, "input after a clear")

    # more commands in one frame than the game keeps: every one is answered, none is lost
    r.send(*[{"cmd": "state", "id": i} for i in range(200)])
    replies = [r.read() for _ in range(200)]
    check(sorted(m["id"] for m in replies) == list(range(200)), "200 commands at once: 200 replies")
    busy = [m for m in replies if m["type"] == "error"]
    check(all("too many commands" in m["error"] for m in busy) and len(busy) < 200, "the surplus is refused by name", busy[:1])
    done = [m["id"] for m in replies if m["type"] == "state"]
    check(done == sorted(done), "the others are answered in order", done)
    r.close()
    print("  ok: limits (10 inputs, %d of 200 commands in one frame refused), clear" % len(busy))


def test_connections(port):
    first = Raw(port)
    at = pos(first.state())
    # a second connection is told why and closed; the first one goes on
    second = Raw(port)
    reply = second.read()
    check(isinstance(reply, dict) and "another agent" in reply.get("error", ""), "the second connection is refused", reply)
    check(second.read() is None, "and closed")
    second.close()
    cli = subprocess.run([sys.executable, CLIENT, "state", "--port", str(port)], capture_output=True, text=True, timeout=60)
    check(cli.returncode == 1 and "another agent is connected" in cli.stderr, "the client names the reason", cli.stderr)
    check(pos(first.state()) == at, "the first connection is untouched")

    # input ends with its connection: the next agent starts with nothing queued and gets no reply meant for the last
    check(first.ask({"cmd": "input", "id": "old", "steps": [hold(3600, "right")]}, "queued")["frames"] == 3600, "queued")
    time.sleep(0.2)
    first.close()
    r = Raw(port)
    reply = r.read(1)
    check(reply == "silent", "the next agent is welcome as soon as the last one is gone", reply)
    end = pos(still(r))
    check(at["x"] < end["x"] < at["x"] + 3600, "the input stopped when its connection closed", at, end)
    check(r.read(0.5) == "silent", "no 'done' for the input of another connection")
    r.close()

    # A web page can make a browser send an HTTP request here. Its first line is not JSON: one error, then closed,
    # and the command in its body is never read.
    body = encode_line({"cmd": "input", "steps": [hold(100, "left")]})
    for request in (b"POST / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n\r\n%s" % (port, len(body), body),
                    b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
                    b"x" * 70000 + b"\n" + body):
        web = Raw(port)
        web.sock.sendall(request)
        reply = web.read()
        check(isinstance(reply, dict) and reply.get("type") == "error", "an HTTP request gets one error", reply)
        check(web.read() is None, "and the connection is closed", request[:20])
        web.close()
    r = Raw(port)
    check(pos(still(r)) == end, "nothing of the HTTP requests was played")
    r.close()
    print("  ok: second connection refused, input ends with its connection, HTTP requests are shut out")


def test_command_line(port):
    def run(*args):
        p = subprocess.run([sys.executable, CLIENT] + list(args), capture_output=True, text=True, timeout=120)
        check(p.returncode == 0, "agent_client.py %s failed" % " ".join(args), p.stdout, p.stderr)
        return p.stdout
    at = pos(json.loads(run("state", "--port", str(port))))
    after = pos(json.loads(run("--port", str(port), "do", "5 right; tap confirm 2 3  # two presses")))
    check(after == dict(at, x=at["x"] + 5, confirm=at["confirm"] + 2), "'do' from the command line", at, after)
    check(run("clear", "--port=%d" % port).strip() == "cleared", "'clear' from the command line")

    # more steps than one command holds, and more commands than the queue: still frame after frame
    with AgentClient(port=port, timeout=20) as game:
        s = game.do("; ".join(["1 right", "1 up"] * 400))
        check(pos(s) == dict(after, x=after["x"] + 400, y=after["y"] - 400), "800 steps in 14 commands", pos(s))
    print("  ok: the command line, and a script of 800 steps")


def test_chapter_change(port, game):
    # Before a chapter change the game closes the port, and the next game process opens it again. The stand-in game
    # does both itself. The agent still holds its old connection meanwhile: that must not keep the port busy.
    old = Raw(port)
    at = pos(old.state())
    check(old.state()["restarts"] == 0, "no restart yet")
    old.send({"cmd": "input", "steps": [hold(1, "down", "right", "up", "left")]})
    reply = old.read()
    check(isinstance(reply, dict) and reply.get("type") == "queued", "the last input before the change", reply)
    check(old.read(10) is None, "the game closes the connection when it closes the port")
    client = connect(game, port, seconds=20)        # the old socket is still open on this side
    s = client.state()
    check(s["restarts"] == 1 and s["start_tries"] == 1, "the port must be free for the first try to listen again", s["restarts"], s["start_tries"])
    check(pos(still(client)) == at, "nothing is played after the change")
    old.close()
    check(pos(client.do("3 right")) == dict(at, x=at["x"] + 3), "the new connection plays")
    client.close()
    print("  ok: the port is closed and opened again as at a chapter change, with the old connection still held")


def quit_game(port, game):
    r = Raw(port)
    r.send({"cmd": "input", "steps": [hold(1, *ALL)]})
    game.wait_exit()
    r.close()


def solo(tmp):
    port = free_port()
    game = Game(tmp, "solo", port, fps=200)
    client = connect(game, port)
    test_basics(port, client)
    client.close()
    test_ids_and_order(port)
    test_refused(port)
    test_limits_and_clear(port)
    test_connections(port)
    test_command_line(port)
    test_chapter_change(port, game)
    quit_game(port, game)


# ---- long replies ----

def long_replies(tmp):
    port = free_port()
    size = 300000
    game = Game(tmp, "solo", port, fps=100, pad=size)
    client = connect(game, port)
    s = client.state()
    check(len(s["pad"]) == size, "a state of 300 KB arrives whole", len(s["pad"]))
    s2 = client.do("3 right; 2 up")
    check(len(s2["pad"]) == size and pos(s2) == dict(pos(s), x=3, y=-2), "and so does a 'done'", pos(s2))
    client.close()

    # 3 MB of replies asked for at once: more than the socket takes in one go, sent as the agent reads
    r = Raw(port)
    r.send(*[{"cmd": "state", "id": i} for i in range(10)])
    replies = [r.read(60) for _ in range(10)]
    check([m["id"] for m in replies] == list(range(10)) and all(len(m["state"]["pad"]) == size for m in replies),
          "ten long replies in a row arrive whole and in order")
    r.close()

    # An agent that asks for more than it reads: the game keeps at most 4 MB of replies for it, then drops it.
    r = Raw(port)
    r.send(*[{"cmd": "state"}] * 25)        # 7.5 MB
    time.sleep(1)
    got, end = 0, time.monotonic() + 60
    r.sock.settimeout(10)
    while True:
        check(time.monotonic() < end, "the game did not drop the agent that does not read")
        try:
            data = r.sock.recv(1 << 20)
        except socket.timeout:
            raise AssertionError("the game did not drop the agent that does not read (%d bytes came)" % got)
        except OSError:
            break
        if not data:
            break
        got += len(data)
    check(got < 25 * size, "the replies beyond the 4 MB were never sent", got)
    r.close()
    client = connect(game, port, seconds=10)
    check(len(client.state()["pad"]) == size, "the game goes on and takes the next agent")
    client.close()
    quit_game(port, game)
    print("  ok: 300 KB replies arrive whole, ten in a row too; an agent that lets 7.5 MB of them wait is dropped")


# ---- online ----

def online(tmp, delay, lag):
    auto = delay < 0
    ports = [free_port(), free_port()]
    host = Game(tmp, "host", ports[0], fps=30, delay=delay, lag=lag, wave=1 if auto else 0)
    time.sleep(1)
    guest = Game(tmp, "client", ports[1], fps=30, delay=delay, lag=lag)
    a1 = connect(host, ports[0], timeout=60)
    a2 = connect(guest, ports[1], timeout=60)
    s = a1.state()
    check(s["mode"] == "host" and a2.state()["mode"] == "client", "both sides run", s)

    # With a fixed delay every frame of an input is applied exactly once. With an automatic one a step may last
    # longer when the delay grows in its last frames, never shorter; presses are exact either way. `moved` names
    # what the input moves and which way: only there, and only that way, may the result go beyond `want`.
    def reached(got, want, moved, what):
        for k in want:
            beyond = (got[k] - want[k]) * moved.get(k, 0)
            check(got[k] == want[k] or (auto and 0 < beyond <= 6), what + " (%s)" % k, got, want)

    s = a1.do("30 right")
    reached(pos(s, 1), {"player": 1, "x": 30, "y": 0, "confirm": 0, "cancel": 0, "menu": 0}, {"x": 1},
            "the host's 'done' must show all 30 frames applied, also the ones still delayed when the last was sent")
    check(pos(still(a1), 1) == pos(s, 1), "nothing of an input is applied after its 'done'")
    p1 = pos(s, 1)
    s = a2.do("20 up; 5 left; tap confirm 2")
    reached(pos(s, 2), {"player": 2, "x": -5, "y": -20, "confirm": 2, "cancel": 0, "menu": 0}, {"x": -1, "y": -1}, "the client's 'done'")
    check(pos(s, 1) == p1, "the client saw the host's input", pos(s, 1), p1)
    p2 = pos(s, 2)

    # both agents at once
    ids = [a._send({"cmd": "input", "steps": steps}) for a, steps in ((a1, [hold(40, "down")]), (a2, [hold(25, "right"), hold(1, "menu")]))]
    d1 = a1._wait("done", ids[0], 30)["state"]
    d2 = a2._wait("done", ids[1], 30)["state"]
    reached(pos(d1, 1), dict(p1, y=40), {"y": 1}, "both agents at once, the host")
    reached(pos(d2, 2), dict(p2, x=p2["x"] + 25, menu=1), {"x": 1}, "both agents at once, the client")
    e1, e2 = still(a1, 0.5), still(a2, 0.5)
    check(e1["players_pos"] == e2["players_pos"], "both sides end with the same positions", e1["players_pos"], e2["players_pos"])
    used = e1["delay"]

    if auto:
        # 12 seconds of taps and short walks while the host's latency comes and goes: the delay grows and shrinks
        # under the input, and still every press arrives as one press.
        before = e1
        after = a1.do("; ".join(["tap confirm 1 1", "8 left"] * 36))
        grown = after["delay_grown"] - before["delay_grown"]
        shrunk = after["delay_shrunk"] - before["delay_shrunk"]
        check(grown > 0 and shrunk > 0, "the delay must have grown and shrunk while the input was played, or this tests nothing",
              grown, shrunk)
        check(pos(after, 1) == dict(pos(before, 1), x=pos(after, 1)["x"], confirm=pos(before, 1)["confirm"] + 36),
              "36 taps must be 36 presses, whatever the delay does, and nothing else may move", pos(before, 1), pos(after, 1), grown, shrunk)
        walked = pos(before, 1)["x"] - pos(after, 1)["x"]
        check(288 <= walked <= 288 + grown, "36 walks of 8 frames: no frame lost, and no more added than the delay grew", walked, grown)
        e1, e2 = still(a1, 0.5), still(a2, 0.5)
        check(pos(e1, 1) == pos(after, 1) and e1["players_pos"] == e2["players_pos"], "both sides agree after it", e1["players_pos"], e2["players_pos"])
        used = "automatic: it grew by %d and shrank by %d frames under the input" % (grown, shrunk)
    else:
        check(used == delay, "the test must run with the delay it asked for", used)

    r = Raw(ports[1])       # refused: a2 is connected
    check("another agent" in r.read().get("error", ""), "one agent per game, online too")
    r.close()
    a2.close()
    a1.sock.sendall(encode_line({"cmd": "input", "steps": [hold(1, *ALL)]}))
    host.wait_exit()
    guest.wait_exit()
    a1.close()
    print("  ok: 'done' waited for the delayed frames, both sides agree (delay %s)" % used)


def main(argv):
    want = argv or ["solo", "long", "online"]
    if not set(want) <= {"solo", "long", "online"}:
        print(__doc__)
        return 2
    if not all(os.path.isfile(os.path.join(BUILD, f)) for f in ("drnet.dll", "test_agent.exe")):
        print("build first: sh native/build.sh")
        return 2
    where = "natively on Windows" if os.name == "nt" else "under %s" % (os.environ.get("WINE") or "wine")
    if os.name != "nt":     # create the Wine prefix before two games race for it
        cmd, env = runner()
        if subprocess.run(cmd + ["cmd", "/c", "exit"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode != 0:
            print("warning: creating the Wine prefix %s failed" % env["WINEPREFIX"])
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        runs = [("solo", "one game", lambda: solo(tmp)), ("long", "long replies", lambda: long_replies(tmp)),
                ("online", "host and client, delay 3", lambda: online(tmp, 3, 0)),
                ("online", "host and client, automatic delay, 60 ms test latency that comes and goes", lambda: online(tmp, -1, 60))]
        for key, title, run in runs:
            if key not in want:
                continue
            print("== Agent API, %s, %s" % (title, where))
            sys.stdout.flush()
            try:
                run()
            except Exception:
                failed += 1
                traceback.print_exc()
                for g in games:
                    if g.proc.poll() is None or g.proc.returncode != 0:
                        print("  -- output of the %s:\n%s" % (g.name, "\n".join("    " + line for line in g.output().splitlines()[-15:])))
            for g in games:
                g.stop()
            del games[:]
    print("FAIL: %d part(s) of the Agent API test failed" % failed if failed else "OK: the Agent API answers as docs/agent-api.md says")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

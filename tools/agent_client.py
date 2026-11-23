#!/usr/bin/env python3
"""Client for the Agent API: read the game state as JSON and send input, from a program on the same PC.

With agent=<port> in dronline.ini the patched game listens on 127.0.0.1:<port> (docs/agent-api.md has the protocol
and the keys of the state). Tested against drnet.dll without the game (native/test_agent.py); in the game itself
it has been tried once, in mode=solo.

  python3 tools/agent_client.py state                             print the game state
  python3 tools/agent_client.py do "20 right; tap confirm 2 15"   send input, wait until the game has applied it,
                                                                  print the state after it
  python3 tools/agent_client.py clear                             drop the input that is still queued

  --port <port>      the agent= port of dronline.ini (default 8765)
  --host <address>   default 127.0.0.1; the game listens only there
  --timeout <s>      how long to wait for the game's answer (default 10). A 'do' waits that long on top of the time
                     its input takes to play
The options may stand before or after the command.

The steps of 'do' are written as in tools/input_script.py, separated by ';' or line breaks:
  <frames> <buttons>            hold <buttons> for <frames> input frames ('-' = nothing held)
  wait <frames>                 nothing held
  tap <buttons> [times] [gap]   press for 1 frame, then nothing for <gap> frames (defaults: 1 time, gap 1)
<frames> are frame counts (30 per second) or seconds like 1.5s. <buttons> are joined with '+': down right up left
confirm cancel menu (or d r u l z x c). The agent has only these seven buttons, not the mod's teleport f1 f2 f3.

Exit code: 0 done, 1 no answer from the game or it refused the command, 2 the command line or the steps are wrong.

As a module (standard library only, so an agent can copy or import this one file plus input_script.py):

    from agent_client import AgentClient
    with AgentClient(port=8765) as game:
        state = game.state()
        if state["can_move"]:
            state = game.do("20 right; tap confirm")

Works from Linux against the game under Proton: Wine's sockets are sockets of the host. One agent at a time: while
one is connected, the game refuses a second connection.
"""
import argparse
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import input_script  # noqa: E402

AGENT_BUTTONS = input_script.BUTTONS[:7]    # action bits 0 to 6; the higher bits belong to the mod and the network
# Limits of the game side (native/drnet.c, AGENT_MAX_*; tests/test_agent_client.py checks that they match).
MAX_STEPS = 60          # steps in one input command
MAX_FRAMES = 3600       # frames in one step
MAX_QUEUED = 10         # input commands in the game's queue
MAX_DELAY = 30          # frames the lockstep can hold input back before the game applies it
DEFAULT_PORT = 8765


class AgentError(RuntimeError):
    """The game refused a command, closed the connection, or did not answer in time."""


class AgentTimeout(AgentError):
    """No answer in time. The connection is still open."""


def encode_line(obj):
    """One message as the bytes of one line. Plain ASCII: every other character goes out as a \\u escape."""
    return json.dumps(obj, separators=(",", ":")).encode("ascii") + b"\n"


def decode_line(line):
    return json.loads(line.decode("utf-8", errors="replace"))


class LineReader:
    """Cuts the bytes that arrive on the socket into lines; a line may come in any number of pieces."""

    def __init__(self):
        self.buf = b""

    def feed(self, data):
        """Adds bytes and returns the lines that are complete now, without their line ends and without empty ones."""
        self.buf += data
        lines = self.buf.split(b"\n")
        self.buf = lines.pop()
        return [line.rstrip(b"\r") for line in lines if line.strip()]


def text_to_frames(text):
    """Steps as text (';' or a line break between them, '#' starts a comment) -> the action bits of every frame."""
    frames = []
    n = 0
    for line in text.splitlines():
        for step in line.split("#", 1)[0].split(";"):
            if not step.strip():
                continue
            n += 1
            try:
                frames += input_script.compile_text(step)
            except input_script.ScriptError as e:
                raise input_script.ScriptError("step %d (%s): %s" % (n, step.strip(), str(e).split(": ", 1)[-1]))
    return frames


def frames_to_steps(frames):
    """Per-frame action bits -> the steps of an input command: equal frames in a row become one step."""
    steps = []
    i = 0
    while i < len(frames):
        bits = frames[i]
        if bits >> len(AGENT_BUTTONS):
            raise input_script.ScriptError("the agent has only the buttons %s, not %s" % (
                " ".join(AGENT_BUTTONS), input_script.button_names(bits >> len(AGENT_BUTTONS) << len(AGENT_BUTTONS))))
        j = i
        while j < len(frames) and frames[j] == bits and j - i < MAX_FRAMES:
            j += 1
        steps.append({"buttons": [b for k, b in enumerate(AGENT_BUTTONS) if bits & (1 << k)], "frames": j - i})
        i = j
    return steps


def text_to_steps(text):
    return frames_to_steps(text_to_frames(text))


def check_steps(steps):
    """Raises ValueError for steps the game would refuse; returns their frames."""
    total = 0
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("a step must be like {\"buttons\": [\"right\"], \"frames\": 30}, not %r" % (step,))
        frames, buttons = step.get("frames"), step.get("buttons")
        if not isinstance(frames, int) or isinstance(frames, bool) or not 1 <= frames <= MAX_FRAMES:
            raise ValueError("frames must be a whole number from 1 to %d, not %r" % (MAX_FRAMES, frames))
        if not isinstance(buttons, (list, tuple)) or any(b not in AGENT_BUTTONS for b in buttons):
            raise ValueError("buttons must be a list of %s, not %r" % (", ".join(AGENT_BUTTONS), buttons))
        total += frames
    return total


def chunk_steps(steps):
    """Steps -> the lists of steps of as many input commands as they need (at most MAX_STEPS each)."""
    return [steps[i:i + MAX_STEPS] for i in range(0, len(steps), MAX_STEPS)]


class AgentClient:
    """A connection to the game. Every method waits for the game's answer and raises AgentError without one."""

    def __init__(self, host="127.0.0.1", port=DEFAULT_PORT, timeout=10.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock = None
        self._reader = LineReader()
        self._inbox = []        # messages that arrived while another one was waited for
        self._next_id = 0

    def connect(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise AgentError("cannot connect to the game on %s:%d (%s): is it running with agent=%d in dronline.ini?"
                             % (self.host, self.port, e, self.port))
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._reader, self._inbox = LineReader(), []
        return self

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()
        return False

    # ---- messages ----

    def _send(self, obj):
        """Sends a command under a new id and returns the id."""
        if self.sock is None:
            raise AgentError("not connected")
        self._next_id += 1
        obj = dict(obj, id=self._next_id)
        try:
            self.sock.sendall(encode_line(obj))
        except OSError as e:
            # The game may have said why it closed the connection ("another agent is connected") before it did.
            why = "the game closed the connection (%s)" % e
            self._wait("error", None, 1.0, closed=why)
            raise AgentError(why)
        return self._next_id

    def _receive(self, deadline, closed=None):
        """Reads from the socket until at least one more message is in the inbox."""
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise AgentError(closed) if closed else AgentTimeout("no answer from the game in time")
            self.sock.settimeout(left)
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as e:
                raise AgentError(closed or "the connection to the game broke (%s)" % e)
            if not data:
                raise AgentError(closed or "the game closed the connection")
            lines = self._reader.feed(data)
            if lines:
                self._inbox += [decode_line(line) for line in lines]
                return

    def _wait(self, kind, rid, seconds, closed=None):
        """Returns the message of that type and id; other messages stay in the inbox for whoever waits for them.
        An error for this command, or one without an id (the line itself was refused), raises AgentError."""
        deadline = time.monotonic() + seconds
        seen = 0
        while True:
            for i in range(seen, len(self._inbox)):
                msg = self._inbox[i]
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") == "error" and msg.get("id") in (rid, None):
                    del self._inbox[i]
                    raise AgentError(str(msg.get("error", "the game answered with an error")))
                if msg.get("type") == kind and msg.get("id") == rid:
                    del self._inbox[i]
                    return msg
            del self._inbox[:-100]      # answers nobody waits for any more (a 'do' that gave up)
            seen = len(self._inbox)
            self._receive(deadline, closed)

    def _answer(self, kind, rid):
        """The answer the game gives in its next frame."""
        try:
            return self._wait(kind, rid, self.timeout)
        except AgentTimeout:
            raise AgentTimeout("no answer from the game within %g s: is it paused, loading or minimized?" % self.timeout)

    # ---- commands ----

    def state(self):
        """The game state (a dict, keys in docs/agent-api.md) as it is at the start of the next game frame."""
        return self._answer("state", self._send({"cmd": "state"}))["state"]

    def clear(self):
        """Drops the input that is still queued in the game. Inputs sent before get no answer any more."""
        self._answer("cleared", self._send({"cmd": "clear"}))

    def send_steps(self, steps):
        """Plays steps like [{"buttons": ["right"], "frames": 30}, ...] and returns the state after the game has
        applied the last frame. Any number of steps: they go out as several input commands, the next one always
        before the game runs out of input, so the frames follow each other without a gap."""
        steps = list(steps)
        total = check_steps(steps)
        if not steps:
            return self.state()
        chunks = chunk_steps(steps)
        waiting = []                                # ids of the commands in the game's queue, oldest first
        sent = 0
        state = None
        # A frame is 1/30 s, and online the input is applied up to MAX_DELAY frames after it was taken. The game
        # may also wait for the other players, so allow twice that time, and the timeout on top.
        patience = self.timeout + (total + MAX_DELAY) / input_script.FPS * 2
        while sent < len(chunks) or waiting:
            while sent < len(chunks) and len(waiting) < MAX_QUEUED:
                rid = self._send({"cmd": "input", "steps": chunks[sent]})
                self._answer("queued", rid)
                waiting.append(rid)
                sent += 1
            try:
                state = self._wait("done", waiting.pop(0), patience)["state"]
            except AgentTimeout:
                raise AgentTimeout("the game has not finished the input after %.0f s: is it paused or waiting for "
                                   "another player? The input is still queued ('clear' drops it)" % patience)
        return state

    def do(self, text):
        """Plays steps written as text ("20 right; tap confirm 2 15") and returns the state after them."""
        return self.send_steps(text_to_steps(text))


def format_state(state, encoding):
    """The state as indented JSON that a terminal with that encoding can show."""
    text = json.dumps(state, indent=2, ensure_ascii=False)
    try:
        text.encode(encoding or "ascii")
    except (UnicodeError, LookupError):
        text = json.dumps(state, indent=2)
    return text


def parse_args(argv):
    # The connection options are accepted before and after the command. SUPPRESS: an option left out after the
    # command must not overwrite the value given before it.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--host", help="default 127.0.0.1")
    common.add_argument("--port", type=int, help="the agent= port of dronline.ini (default %d)" % DEFAULT_PORT)
    common.add_argument("--timeout", type=float, help="seconds to wait for the game's answer (default 10)")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, parents=[common])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("state", parents=[common], help="print the game state")
    p_do = sub.add_parser("do", parents=[common], help="send input, wait until it is done, print the state")
    p_do.add_argument("steps", help='like "20 right; tap confirm 2 15"')
    sub.add_parser("clear", parents=[common], help="drop the input that is still queued")
    args = ap.parse_args(argv)
    for name, default in (("host", "127.0.0.1"), ("port", DEFAULT_PORT), ("timeout", 10.0)):
        if not hasattr(args, name):
            setattr(args, name, default)
    return args


def main(argv):
    args = parse_args(argv)
    try:
        steps = text_to_steps(args.steps) if args.command == "do" else None
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    try:
        with AgentClient(args.host, args.port, args.timeout) as game:
            if args.command == "clear":
                game.clear()
                print("cleared")
            else:
                state = game.state() if args.command == "state" else game.send_steps(steps)
                print(format_state(state, sys.stdout.encoding))
    except AgentError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

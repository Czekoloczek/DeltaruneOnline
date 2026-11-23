#!/usr/bin/env python3
"""Agent API client for DeltaruneOnline (untested draft).

The game opens a TCP listener on 127.0.0.1 when `agent=<port>` is set in dronline.ini.
Messages are JSON objects, one per line. This client sends `state`, `input` and `clear`
commands and prints the state returned by a `done` reply.

Usage:
  python3 tools/agent_client.py state --port 8765
  python3 tools/agent_client.py do "20 right; tap confirm 2 15" --port 8765

The `do` command accepts the same text syntax as tools/input_script.py, but only the seven
action buttons (down right up left confirm cancel menu) are allowed.
"""
import argparse
import json
import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.input_script import BUTTONS, compile_text, button_names, ScriptError  # noqa: E402


class AgentError(RuntimeError):
    pass


def encode_line(obj):
    return json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\n"


def decode_line(line):
    return json.loads(line.decode("utf-8"))


def text_to_steps(text):
    """Compile input_script.py text and group consecutive equal frames into steps."""
    frames = compile_text(text)
    steps = []
    i = 0
    while i < len(frames):
        bits = frames[i]
        if bits & ~0x7F:
            raise ValueError("agent input may only use the seven action buttons")
        names = button_names(bits)
        buttons = [] if names == "-" else names.split("+")
        j = i + 1
        while j < len(frames) and frames[j] == bits:
            j += 1
        steps.append({"buttons": buttons, "frames": j - i})
        i = j
    return steps


class AgentClient:
    def __init__(self, host="127.0.0.1", port=8765, timeout=10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self._file = None
        self._next_id = 0

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._file = self.sock.makefile("rb")
        return self

    def close(self):
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None
        self._file = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()
        return False

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    def _send_obj(self, obj):
        self.sock.sendall(encode_line(obj))

    def _read_obj(self):
        while True:
            line = self._file.readline()
            if not line:
                raise EOFError("agent closed the connection")
            if line.strip():
                return decode_line(line)

    def _wait_for(self, wait_type, rid):
        while True:
            msg = self._read_obj()
            if msg.get("type") == "error":
                raise AgentError(msg.get("error", "agent error"))
            if msg.get("type") == wait_type and msg.get("id") == rid:
                return msg

    def state(self):
        rid = self._new_id()
        self._send_obj({"cmd": "state", "id": rid})
        return self._wait_for("state", rid)["state"]

    def clear(self):
        rid = self._new_id()
        self._send_obj({"cmd": "clear", "id": rid})
        self._wait_for("cleared", rid)
        return None

    def input_text(self, text):
        return self.input_steps(text_to_steps(text))

    def input_steps(self, steps):
        rid = self._new_id()
        self._send_obj({"cmd": "input", "id": rid, "steps": steps})
        self._wait_for("queued", rid)
        done = self._wait_for("done", rid)
        return done["state"]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--timeout", type=float, default=10.0)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("state", help="read and print the game state")
    p_do = sub.add_parser("do", help="send input steps and print the state at done")
    p_do.add_argument("steps")
    args = ap.parse_args(argv)
    with AgentClient(args.host, args.port, args.timeout) as client:
        if args.command == "state":
            print(json.dumps(client.state(), indent=2))
        elif args.command == "do":
            try:
                print(json.dumps(client.input_text(args.steps), indent=2))
            except (ValueError, ScriptError) as e:
                ap.exit(2, "error: %s\n" % e)
        else:
            ap.error("unknown command %s" % args.command)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

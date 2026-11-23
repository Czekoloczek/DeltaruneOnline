"""Unit tests for tools/agent_client.py (no game, no network)."""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.agent_client import encode_line, decode_line, text_to_steps  # noqa: E402


class StepConversionTests(unittest.TestCase):
    def test_single_hold(self):
        self.assertEqual(text_to_steps("20 right"), [{"buttons": ["right"], "frames": 20}])

    def test_no_buttons(self):
        self.assertEqual(text_to_steps("wait 5"), [{"buttons": [], "frames": 5}])

    def test_group_consecutive(self):
        self.assertEqual(
            text_to_steps("20 right; 5 right"),
            [{"buttons": ["right"], "frames": 25}],
        )

    def test_tap(self):
        self.assertEqual(
            text_to_steps("tap confirm 2 15"),
            [
                {"buttons": ["confirm"], "frames": 1},
                {"buttons": [], "frames": 15},
                {"buttons": ["confirm"], "frames": 1},
                {"buttons": [], "frames": 15},
            ],
        )

    def test_rejects_non_agent_buttons(self):
        with self.assertRaises(ValueError):
            text_to_steps("20 teleport")


class JsonLineFramingTests(unittest.TestCase):
    def test_roundtrip(self):
        obj = {"cmd": "state", "id": 7}
        self.assertEqual(encode_line(obj), b'{"cmd":"state","id":7}\n')
        self.assertEqual(decode_line(encode_line(obj)), obj)

    def test_escaped_text(self):
        obj = {"type": "error", "error": 'a"b\\c\n'}
        decoded = decode_line(encode_line(obj))
        self.assertEqual(decoded, obj)


if __name__ == "__main__":
    unittest.main()

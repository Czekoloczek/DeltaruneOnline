"""tools/input_script.py: text scripts, the per-frame file the game reads (script=), and how both match the GML."""
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import input_script as s  # noqa: E402

with open(os.path.join(ROOT, "gml", "scr_dronline.gml"), encoding="utf-8") as f:
    GML = f.read()

D, R, U, L, Z, X, C = 1, 2, 4, 8, 16, 32, 64


class Compile(unittest.TestCase):
    def test_hold_wait_and_comments(self):
        text = "# intro\nwait 3\n2 right   # walk\n1 up+cancel\n\n2 -\n"
        self.assertEqual(s.compile_text(text), [0, 0, 0, R, R, U | X, 0, 0])

    def test_aliases_and_case(self):
        self.assertEqual(s.compile_text("1 Z\n1 d+R+u+L\n1 x+c"), [Z, D | R | U | L, X | C])

    def test_mod_buttons(self):
        self.assertEqual(s.compile_text("1 teleport+f1+f2+f3"), [128 | 256 | 512 | 1024])

    def test_seconds(self):
        self.assertEqual(len(s.compile_text("wait 1.5s")), 45)
        self.assertEqual(s.compile_text("0.1s confirm"), [Z] * 3)

    def test_tap(self):
        self.assertEqual(s.compile_text("tap z"), [Z, 0])
        self.assertEqual(s.compile_text("tap z 3"), [Z, 0] * 3)
        self.assertEqual(s.compile_text("tap z 2 3"), [Z, 0, 0, 0] * 2)
        self.assertEqual(s.compile_text("tap right 2 0"), [R, R])

    def test_errors_name_the_line(self):
        for text, word in [("1 jump", "unknown button"), ("0 z", "more than 0"), ("x z", "number"),
                           ("wait", "wait takes"), ("tap", "tap <buttons>"), ("1 z 2", "expected"),
                           ("tap z 0", "more than 0"), ("-1 z", "more than 0")]:
            with self.subTest(text=text):
                with self.assertRaises(s.ScriptError) as cm:
                    s.compile_text("wait 1\n" + text)
                self.assertIn("line 2", str(cm.exception))
                self.assertIn(word, str(cm.exception))


class FileFormat(unittest.TestCase):
    def test_little_endian_u16_per_frame(self):
        self.assertEqual(s.to_bytes([Z, 1024 | 1]), b"\x10\x00\x01\x04")

    def test_dump_compiles_back_to_the_same_bytes(self):
        rnd = random.Random(5)
        frames = []
        for _ in range(300):
            frames += [rnd.choice([0, 0, Z, R, R | X, 1024, s.MAX_BITS])] * rnd.randint(1, 40)
        data = s.to_bytes(frames)
        self.assertEqual(s.to_bytes(s.compile_text(s.dump_text(s.from_bytes(data)))), data)

    def test_rejects_files_that_are_not_input(self):
        with self.assertRaises(s.ScriptError):
            s.from_bytes(b"\x01")
        with self.assertRaises(s.ScriptError):
            s.from_bytes(s.to_bytes([2048]))  # bit 11 belongs to the network

    def test_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            txt, out = os.path.join(d, "a.txt"), os.path.join(d, "a.bin")
            with open(txt, "w", encoding="utf-8") as f:
                f.write("wait 2\ntap z 2\n")
            tool = os.path.join(ROOT, "tools", "input_script.py")
            subprocess.run([sys.executable, tool, "compile", txt, out], check=True, capture_output=True)
            with open(out, "rb") as f:
                self.assertEqual(f.read(), s.to_bytes([0, 0, Z, 0, Z, 0]))
            dump = subprocess.run([sys.executable, tool, "dump", out], check=True, capture_output=True, text=True)
            self.assertEqual(dump.stdout, "2 -\n1 confirm\n1 -\n1 confirm\n1 -\n")
            bad = subprocess.run([sys.executable, tool, "compile", out, txt], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("error:", bad.stderr)


class MatchesTheGame(unittest.TestCase):
    def test_bit_layout_is_the_one_dronline_local_bits_builds(self):
        # gml/scr_dronline.gml documents the layout above dronline_local_bits
        self.assertIn("// bit 0 down, 1 right, 2 up, 3 left, 4 confirm, 5 cancel, 6 menu,", GML)
        self.assertIn("// bit 7 teleport (mod), 8..10 F1..F3 player amount (mod)", GML)
        self.assertEqual(s.BUTTONS[:7], ["down", "right", "up", "left", "confirm", "cancel", "menu"])
        self.assertEqual(s.BUTTONS[7:], ["teleport", "f1", "f2", "f3"])
        # and the network keeps bit 11 for itself (raw & 2047), which the script reader masks the same way
        self.assertIn("global.dro_bits[p] = raw & 2047;", GML)
        self.assertIn("buffer_peek(global.dro_script, global.dro_script_pos, buffer_u16) & 2047", GML)
        self.assertEqual(s.MAX_BITS, 2047)

    def test_game_reads_and_records_u16(self):
        self.assertIn("buffer_write(global.dro_srec, buffer_u16, mine);", GML)

    def test_recording_name_has_the_role(self):
        # copies on one PC share the save folder: the role keeps their recordings apart (dronline_rec_host.bin)
        self.assertIn("global.dro_role = role;", GML)
        self.assertIn('+ "_" + global.dro_role + ext;', GML)
        init = GML[GML.index("function dronline_script_init()"):]
        self.assertLess(init.index("global.dro_role + ext"), init.index("var f = global.dro_srec_file;"))

    def test_ini_keys_are_documented(self):
        with open(os.path.join(ROOT, "config", "dronline.ini.example"), encoding="utf-8") as f:
            example = f.read()
        for key in ("script", "script_end", "script_record"):
            with self.subTest(key=key):
                self.assertRegex(GML, r'ini_read_string\("online", "%s"' % key)
                self.assertRegex(example, r"(?m)^;?\s*%s=" % key)

    def test_script_end_values(self):
        values = set(re.findall(r'global\.dro_script_end [!=]= "(\w+)"', GML))
        self.assertEqual(values, {"quit", "keys"})  # anything else, including the default "idle", means no input


if __name__ == "__main__":
    unittest.main()

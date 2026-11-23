"""Cross-file checks for mistakes that compile fine and only show up in the game.

- Every dro_* function the patcher rewrites game code to, and every hook it inserts, must exist in
  gml/scr_dronline.gml (otherwise the game fails at runtime on the first call).
- Every drnet_* function the GML calls must be exported by native/drnet.c AND registered in
  patcher/utmt/add_extension.csx with the same arguments ("Extension functions must also be in the FUNC
  table"), and a DLL function with more than 4 arguments must take numbers only (docs/DESIGN.md, Pitfalls).
- Every option name the GML passes to drnet_option must be one that drnet.c knows (it ignores the others).
- Every dronline.ini key the GML reads must be in config/dronline.ini.example and in the settings comment at the
  top of gml/scr_dronline.gml (CLAUDE.md).
- A dro_* function that stands in for an engine function with a return value must return a value on every path,
  online too. The game may use the value: Chapter 5's canopy shadows seed the generator with what randomize()
  returns, and an online path that returned nothing crashed both machines in the same frame.
"""
import os
import re
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "patcher"))
import dronline_patch as dp  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


GML = read("gml", "scr_dronline.gml")
DRNET_C = read("native", "drnet.c")
EXT_CSX = read("patcher", "utmt", "add_extension.csx")
PATCHER = read("patcher", "dronline_patch.py")
INI_EXAMPLE = read("config", "dronline.ini.example")

GML_FUNCS = set(re.findall(r"^function\s+(\w+)\s*\(", GML, re.M))


def c_exports():
    """name -> list of argument kinds ('D' number, 'S' string) and the return kind."""
    out = {}
    for ret, name, args in re.findall(r"^EXPORT\s+(double|const char \*)\s*(\w+)\s*\(([^)]*)\)", DRNET_C, re.M):
        kinds = []
        for a in args.split(","):
            a = a.strip()
            if not a or a == "void":
                continue
            kinds.append("S" if "char" in a else "D")
        out[name] = ("S" if "char" in ret else "D", kinds)
    return out


def csx_functions():
    out = {}
    for name, types in re.findall(r'^Fn\("(\w+)",\s*([^)]*)\);', EXT_CSX, re.M):
        kinds = [t.strip() for t in types.split(",")]
        out[name] = (kinds[0], kinds[1:])
    return out


class PatcherTargetsExist(unittest.TestCase):
    def test_rewrite_targets_are_defined(self):
        targets = {re.match(r"(\w+)\(", repl).group(1) for _, repl in dp.RULES}
        missing = sorted(t for t in targets if t not in GML_FUNCS)
        self.assertEqual(missing, [], "rewrite rules point at functions missing from gml/scr_dronline.gml")

    def test_inserted_hooks_are_defined(self):
        called = set(re.findall(r"\b(dronline_\w+)\(", PATCHER))
        generated = {"dro_heart_hooked", "dro_heart_collide_p1"}  # appended to the module by the patcher
        missing = sorted(n for n in called if n not in GML_FUNCS)
        self.assertEqual(missing, [], "patcher inserts calls to functions missing from gml/scr_dronline.gml")
        for name in generated:
            self.assertIn("function " + name, dp.HEART_OFF)


# What the replaced engine functions return (GameMaker manual). A new rewrite rule must be added to one of the
# sets, so that someone decides whether the game can use its result.
RETURNS_VALUE = {
    "keyboard_check", "keyboard_check_pressed", "keyboard_check_released", "keyboard_check_direct",
    "gamepad_button_check", "gamepad_button_check_pressed", "gamepad_button_check_released", "gamepad_axis_value",
    "gamepad_is_connected", "gamepad_get_device_count", "randomize", "get_timer", "ini_read_real",
    "audio_play_sound", "audio_play_sound_on", "audio_sound_get_track_position", "audio_is_playing", "audio_is_paused",
}
RETURNS_NOTHING = {
    "io_clear", "game_change", "game_restart", "audio_sound_set_track_position", "audio_sound_pitch",
    "audio_pause_sound", "audio_resume_sound", "audio_stop_sound", "audio_stop_all", "audio_pause_all",
    "audio_resume_all",
}
# Not checked against the manual yet: whether keyboard_clear returns a value (dro_kb_clear returns nothing online).
UNCHECKED = {"keyboard_clear"}


def gml_body(name):
    """The body of a GML function, from its opening brace to the matching closing one."""
    m = re.search(r"^function\s+%s\s*\([^)]*\)\s*\{" % name, GML, re.M)
    if not m:
        return None
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(GML[i], 0)
        i += 1
    return GML[m.end():i - 1]


def rule_pairs():
    """(engine function, dro_* function) for every rewrite rule."""
    return [(re.match(r"\\b(\w+)", rx.pattern).group(1), re.match(r"(\w+)\(", repl).group(1)) for rx, repl in dp.RULES]


class ReturnValues(unittest.TestCase):
    def test_every_replaced_function_is_classified(self):
        for engine, _ in rule_pairs():
            with self.subTest(engine=engine):
                self.assertIn(engine, RETURNS_VALUE | RETURNS_NOTHING | UNCHECKED,
                              "add it to RETURNS_VALUE or RETURNS_NOTHING (does the engine function return a value?)")

    def test_stand_ins_return_a_value_on_every_path(self):
        for engine, dro in rule_pairs():
            if engine not in RETURNS_VALUE:
                continue
            with self.subTest(engine=engine, stand_in=dro):
                body = gml_body(dro)
                self.assertIsNotNone(body, "function missing from gml/scr_dronline.gml")
                self.assertNotRegex(body, r"\breturn\s*;", "a bare return gives the game undefined")
                last = [l.strip() for l in body.strip().splitlines() if l.strip() and not l.strip().startswith("//")][-1]
                self.assertRegex(last, r"^return\s+\S", "the function can end without a return: the game gets undefined")


class ExtensionMatchesDll(unittest.TestCase):
    def test_gml_calls_are_registered_and_exported(self):
        used = set(re.findall(r"\b(drnet_\w+)\s*\(", GML))
        exports, registered = c_exports(), csx_functions()
        self.assertEqual(sorted(used - exports.keys()), [], "GML calls drnet functions that drnet.c does not export")
        self.assertEqual(sorted(used - registered.keys()), [], "GML calls drnet functions missing from add_extension.csx")

    def test_signatures_match(self):
        exports, registered = c_exports(), csx_functions()
        for name, sig in registered.items():
            with self.subTest(name=name):
                self.assertIn(name, exports, "registered in add_extension.csx but not exported by drnet.c")
                self.assertEqual(sig, exports[name], "return/argument types differ between drnet.c and add_extension.csx")

    def test_more_than_four_arguments_are_numbers(self):
        for name, (_, args) in csx_functions().items():
            if len(args) > 4:
                with self.subTest(name=name):
                    self.assertTrue(all(a == "D" for a in args),
                                    "GameMaker refuses DLL functions with more than 4 arguments unless all are numbers")


class SettingsMatch(unittest.TestCase):
    def test_drnet_options_are_known_to_the_dll(self):
        used = set(re.findall(r'\bdrnet_option\(\s*"(\w+)"', GML))
        body = DRNET_C[DRNET_C.index("EXPORT double drnet_option("):]
        body = body[:body.index("\n}\n")]
        known = set(re.findall(r'!strcmp\(name, "(\w+)"\)', body))
        self.assertTrue(used, "no drnet_option calls found in the GML")
        self.assertEqual(sorted(used - known), [], "the GML sets options that drnet.c silently ignores")

    def test_ini_keys_are_documented(self):
        keys = set(re.findall(r'\bini_read_(?:string|real)\(\s*"online"\s*,\s*"(\w+)"', GML))
        header = GML[:GML.index("function ")]
        self.assertGreater(len(keys), 10, "the [online] keys were not found in the GML")
        for key in sorted(keys):
            with self.subTest(key=key):
                self.assertRegex(INI_EXAMPLE, r"(?m)^;?\s*%s=" % key, "missing from config/dronline.ini.example")
                self.assertRegex(header, r"(?m)^//\s+%s\s*=" % key, "missing from the settings comment in scr_dronline.gml")


if __name__ == "__main__":
    unittest.main()

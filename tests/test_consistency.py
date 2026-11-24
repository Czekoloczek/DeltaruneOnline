"""Cross-file checks for mistakes that compile fine and only show up in the game.

- Every dro_* function the patcher rewrites game code to, and every hook it inserts, must exist in
  gml/scr_dronline.gml (otherwise the game fails at runtime on the first call).
- Every drnet_* function the GML calls must be exported by native/drnet.c AND registered in
  patcher/utmt/add_extension.csx with the same arguments ("Extension functions must also be in the FUNC
  table"), and a DLL function with more than 4 arguments must take numbers only (docs/DESIGN.md, Pitfalls).
- Every option name the GML passes to drnet_option must be one that drnet.c knows (it ignores the others).
- Every dronline.ini key the GML reads must be in config/dronline.ini.example and in the settings comment at the
  top of gml/scr_dronline.gml (CLAUDE.md).
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
        targets |= {re.match(r"(\w+)\(", repl).group(1) for repl in dp.VAR_RULES.values()}
        missing = sorted(t for t in targets if t not in GML_FUNCS)
        self.assertEqual(missing, [], "rewrite rules point at functions missing from gml/scr_dronline.gml")
        for var, repl in dp.VAR_RULES.items():
            out, n = dp.rewrite(var)
            self.assertEqual((out, n), (repl, 1), f"VAR_RULES entry {var!r} is not rewritten by VAR_TOKEN_RX")

    def test_inserted_hooks_are_defined(self):
        called = set(re.findall(r"\b(dronline_\w+)\(", PATCHER))
        generated = {"dro_heart_hooked", "dro_heart_collide_p1"}  # appended to the module by the patcher
        missing = sorted(n for n in called if n not in GML_FUNCS)
        self.assertEqual(missing, [], "patcher inserts calls to functions missing from gml/scr_dronline.gml")
        for name in generated:
            self.assertIn("function " + name, dp.HEART_OFF)


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

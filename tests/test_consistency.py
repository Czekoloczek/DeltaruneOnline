"""Cross-file checks for mistakes that compile fine and only show up in the game.

- Every dro_* function the patcher rewrites game code to, and every hook it inserts, must exist in
  gml/scr_dronline.gml (otherwise the game fails at runtime on the first call).
- Every drnet_* function the GML calls must be exported by native/drnet.c AND registered in
  patcher/utmt/add_extension.csx with the same arguments ("Extension functions must also be in the FUNC
  table"), and a DLL function with more than 4 arguments must take numbers only (docs/DESIGN.md, Pitfalls).
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


if __name__ == "__main__":
    unittest.main()

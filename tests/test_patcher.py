"""Tests for the patcher's rewrite rules and the obj_heart hooks.

The GML snippets below are written for these tests and only imitate the shape of the game's code;
nothing from the game or the mod is stored here. Run: python3 -m unittest discover -s tests -v
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "patcher"))
import dronline_patch as dp  # noqa: E402


class RewriteRules(unittest.TestCase):
    def check(self, src, expected, count):
        out, n = dp.rewrite(src)
        self.assertEqual(out, expected)
        self.assertEqual(n, count)

    def test_keyboard_reads(self):
        self.check("if (keyboard_check_pressed(vk_enter) || keyboard_check(ord(\"Z\")))",
                   "if (dro_kb_pressed(vk_enter) || dro_kb_check(ord(\"Z\")))", 2)
        self.check("a = keyboard_check_released(vk_left); b = keyboard_check_direct(vk_f4);",
                   "a = dro_kb_released(vk_left); b = dro_kb_check(vk_f4);", 2)
        self.check("keyboard_clear(vk_escape); io_clear();", "dro_kb_clear(vk_escape); dro_io_clear();", 2)

    def test_gamepad_reads(self):
        self.check("gamepad_button_check_pressed(0, gp_face1) + gamepad_button_check(0, gp_face2)",
                   "dro_gp_pressed(0, gp_face1) + dro_gp_check(0, gp_face2)", 2)
        self.check("gamepad_axis_value(0, gp_axislh); gamepad_is_connected(1); gamepad_get_device_count();",
                   "dro_gp_axis(0, gp_axislh); dro_gp_connected(1); dro_gp_count();", 3)

    def test_randomize_with_spaces(self):
        self.check("randomize( );", "dro_randomize();", 1)

    def test_clock(self):
        # obj_spin_petal's Draw: the petals' x followed the real clock, so it differed between machines
        self.check("x = xstart + (sin(rando + (get_timer() / 500000)) * 3);",
                   "x = xstart + (sin(rando + (dro_get_timer() / 500000)) * 3);", 1)
        self.check("t = get_timer( );", "t = dro_get_timer();", 1)
        self.check("t = my_get_timer() + get_timerx();", "t = my_get_timer() + get_timerx();", 0)

    def test_audio(self):
        self.check("snd = audio_play_sound_on(em, mus, 1, 0); audio_play_sound(snd_x, 1, 0);",
                   "snd = dro_audio_play_sound_on(em, mus, 1, 0); dro_audio_play_sound(snd_x, 1, 0);", 2)
        self.check("if (!audio_is_playing(s)) audio_stop_all();",
                   "if (!dro_audio_is_playing(s)) dro_audio_stop_all();", 2)

    def test_player_amount_only(self):
        self.check('readval = ini_read_real("PLAYER_AMOUNT", "1", -1);',
                   'readval = dro_ini_player_amount("PLAYER_AMOUNT", "1", -1);', 1)
        self.check('readval = ini_read_real("SHARE_INV", "1", -1);',
                   'readval = ini_read_real("SHARE_INV", "1", -1);', 0)

    def test_word_boundaries(self):
        # names that only contain a rewritten name must stay as they are
        src = "my_keyboard_check(1); scr_randomize(); xaudio_is_playing(2); keyboard_check_pressedx(3)"
        self.check(src, src, 0)

    def test_idempotent(self):
        src = ("if (keyboard_check_pressed(vk_up)) { randomize(); audio_play_sound(a, 1, 0); }\n"
               'n = ini_read_real("PLAYER_AMOUNT", "1", -1);')
        once, _ = dp.rewrite(src)
        twice, n = dp.rewrite(once)
        self.assertEqual(once, twice)
        self.assertEqual(n, 0)


COLL = """if (global.invc > 0)
    exit;
with (other)
{
    if (active == 1)
        hit = 1;
}
global.hp -= other.damage;
"""
STEP = """if (canmove2)
    x2 += 1;
var b2 = instance_place(x2, y2, obj_collidebullet);
var b3 = instance_place( x3 ,y3, obj_collidebullet );
var other_check = instance_place(x, y, obj_collidebullet);
"""
END = "wspeed = 4;\n"
DRAW = "draw_sprite(sprite_index, 0, x, y);\n"


def heart_texts(**over):
    t = {dp.HEART_COLL: COLL, dp.HEART_STEP: STEP, dp.HEART_END: END, dp.HEART_DRAW: DRAW}
    t.update(over)
    return {k: v for k, v in t.items() if v is not None}


class HeartHooks(unittest.TestCase):
    def test_applied(self):
        t = heart_texts()
        gen, note = dp.heart_hooks(t)
        self.assertIn("2 hit checks for hearts 2/3", note)
        self.assertIn("own heart drawn ahead", note)
        self.assertIn("dronline_place(2, x2, y2)", t[dp.HEART_STEP])
        self.assertIn("dronline_place(3, x3, y3)", t[dp.HEART_STEP])
        # heart 1's own check (x, y) is not one of the hooked calls
        self.assertIn("instance_place(x, y, obj_collidebullet)", t[dp.HEART_STEP])
        self.assertEqual(t[dp.HEART_COLL], "if (!dronline_ownhit(1))\n    dro_heart_collide_p1(other);\n")
        self.assertTrue(t[dp.HEART_END].startswith("dronline_heart_endstep();\n"))
        self.assertTrue(t[dp.HEART_DRAW].startswith("dronline_heart_draw(true);\n"))
        self.assertTrue(t[dp.HEART_DRAW].rstrip().endswith("dronline_heart_draw(false);"))
        self.assertIn("function dro_heart_hooked()\n{\n    return true;", gen)
        self.assertIn("function dro_heart_collide_p1(_dro_b)", gen)
        self.assertIn("with (_dro_b)", gen)
        self.assertIn("_dro_b.damage", gen)
        self.assertNotRegex(gen.split("function dro_heart_collide_p1")[1], r"\bother\b")

    def test_draw_with_exit_is_not_wrapped(self):
        draw = "if (!visible)\n    exit;\n" + DRAW
        t = heart_texts(**{dp.HEART_DRAW: draw})
        gen, note = dp.heart_hooks(t)
        self.assertEqual(t[dp.HEART_DRAW], draw)
        self.assertNotIn("drawn ahead", note)
        self.assertIn("return true", gen)

    def test_missing_draw_is_fine(self):
        t = heart_texts(**{dp.HEART_DRAW: None})
        gen, _ = dp.heart_hooks(t)
        self.assertIn("return true", gen)

    def assert_off(self, t):
        before = copy.deepcopy(t)
        gen, _ = dp.heart_hooks(t)
        self.assertEqual(gen, dp.HEART_OFF)
        self.assertEqual(t, before, "hooks must change nothing when they are off")

    def test_all_or_nothing(self):
        self.assert_off(heart_texts(**{dp.HEART_STEP: None}))
        self.assert_off(heart_texts(**{dp.HEART_COLL: COLL.replace("with (other)", "with (obj_heart)")}))
        self.assert_off(heart_texts(**{dp.HEART_COLL: COLL + "function f()\n{\n}\n"}))
        self.assert_off(heart_texts(**{dp.HEART_STEP: "x2 += 1;\n"}))

    def test_off_module_defines_both_functions(self):
        self.assertIn("function dro_heart_hooked()", dp.HEART_OFF)
        self.assertIn("return false;", dp.HEART_OFF)
        self.assertIn("function dro_heart_collide_p1(_dro_b)", dp.HEART_OFF)


if __name__ == "__main__":
    unittest.main()

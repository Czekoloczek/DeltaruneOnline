"""Tests for the patcher's rewrite rules, the obj_heart hooks and the faint SOULs of the other players.

The GML snippets below are written for these tests and only imitate the shape of the game's code;
nothing from the game or the mod is stored here. Run: python3 -m unittest discover -s tests -v
"""
import copy
import os
import re
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
        # a made-up Draw-style line: an instance position that follows the clock must use the frame clock
        self.check("offset = sin(phase + (get_timer() / 400000)) * 2;",
                   "offset = sin(phase + (dro_get_timer() / 400000)) * 2;", 1)
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


class ExportCheck(unittest.TestCase):
    def test_all_entries(self):
        self.assertEqual(dp.export_check("noise\nEXPORT_OK 12345 entries, failed 0\n"), 12345)

    def test_failed_entry_stops(self):
        out = "DECOMPILE_FAIL gml_Object_obj_x_Step_0: oops\nEXPORT_OK 10 entries, failed 1\n"
        with self.assertRaises(SystemExit) as e:
            dp.export_check(out)
        self.assertIn("1 of 10 code entries could not be decompiled", str(e.exception))

    def test_no_report_stops(self):
        with self.assertRaises(SystemExit):
            dp.export_check("something else\n")


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

    def test_draw_with_exit_turns_the_hooks_off(self):
        # the wrap would be skipped, and hits would land where the owner does not see the heart
        for draw in ("if (!visible)\n    exit;\n" + DRAW, DRAW + "return;\n"):
            with self.subTest(draw=draw):
                self.assert_off(heart_texts(**{dp.HEART_DRAW: draw}))

    def test_missing_draw_turns_the_hooks_off(self):
        self.assert_off(heart_texts(**{dp.HEART_DRAW: None}))

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


# Made-up draw code in the shape of the SOUL draws (no game code): what the other_souls rewrite must do.
SOUL_DRAW = """if (color == 2)
{
    draw_sprite_ext(spr_tail, 0, tx + x_offset, ty, image_xscale, image_yscale, 0, c_orange, 0.025);
    draw_sprite_ext(sprite_index, image_index2, x2 + _w2 + x_offset, y2 + _h2, xs, ys, image_angle, image_blend2, image_alpha);
    draw_sprite_ext(spr_arrow, 0, x + (i * 16) + 16, y + 10, 1, 1, 0, draw_get_colour(), 1);
    draw_sprite_ext(spr_x, 0, xx + get(1), yy, 1, 1, 0, c_white, 1);
    draw_text(x2, y2, "a, (b)");
}
else
{
    image_index3 += image_speed3;
    draw_sprite_ext(sprite_index, image_index3, x3, y3, 1, image_yscale, image_angle, image_blend3, 1);
    draw_sprite_part_ext(spr_part, 0, 0, 0, 10, 10, x2 - 5, y2, 1, 1, c_white, a[0]);
    draw_self();
}
"""


class SoulAlpha(unittest.TestCase):
    def run_on(self, **texts):
        t = dict(texts)
        note = dp.soul_alpha(t)
        return t, note

    def test_heart_draws_follow_the_heart_they_draw(self):
        t, note = self.run_on(**{dp.HEART_DRAW: SOUL_DRAW})
        d = t[dp.HEART_DRAW]
        self.assertIn("image_blend2, (image_alpha) * dronline_heart_alpha(2));", d)
        self.assertIn("image_blend3, (1) * dronline_heart_alpha(3));", d)
        self.assertIn("draw_get_colour(), (1) * dronline_heart_alpha(1));", d)
        self.assertIn("c_white, (a[0]) * dronline_heart_alpha(2));", d)
        # a position that names no heart: the nearest one, from the same expressions
        self.assertIn("c_orange, (0.025) * dronline_heart_alpha_at(tx + x_offset, ty));", d)
        # a position with a call is never evaluated twice
        self.assertIn("yy, 1, 1, 0, c_white, 1);", d)
        self.assertIn("dronline_draw_self(dronline_heart_alpha(1));", d)
        self.assertNotIn("draw_self()", d)
        self.assertIn('draw_text(x2, y2, "a, (b)");', d)
        self.assertTrue(note.startswith("obj_heart 6 draws (1 by the nearest heart, 1 left as they are)"))

    def test_only_alpha_changes(self):
        # drawing only: removing the factors gives back the original code, statement for statement
        t, _ = self.run_on(**{dp.HEART_DRAW: SOUL_DRAW})
        back = re.sub(r"\(([^()]*(?:\[[^\]]*\])?)\) \* dronline_heart_alpha(?:_at)?\((?:[^()]|\([^()]*\))*\)", r"\1",
                      t[dp.HEART_DRAW])
        back = back.replace("dronline_draw_self(dronline_heart_alpha(1))", "draw_self()")
        self.assertEqual(back, SOUL_DRAW)

    def test_owner_objects(self):
        graze = "if (controls == 2)\n    draw_sprite_ext(sprite_index, 0, x2 + x_offset, y2, 1, 1, angle, c_white, grazetimer / 6);\ngrazetimer -= 1;\n"
        slime = ("surface_set_target(s);\ndraw_sprite_ext(spr_white, 0, 10, 10, 1, 1, 0, c_white, 1);\nsurface_reset_target();\n"
                 "draw_surface_ext(s, x, y, 1, 1, 0, c_gray, 1);\n")
        orange = "draw_sprite_ext(sprite_index, image_index, x, y, 1, 1, 0, image_blend, image_alpha);\ndraw_self();\n"
        t, note = self.run_on(
            gml_Object_obj_grazebox_Draw_0=graze,
            gml_Object_obj_heart_slime_Draw_0=slime,
            gml_Object_obj_orangeheart_Draw_0=orange,
        )
        self.assertIn("c_white, (grazetimer / 6) * dronline_soul_alpha_controls(1));\ngrazetimer -= 1;", t["gml_Object_obj_grazebox_Draw_0"])
        # the slime is drawn into a surface first: only the surface on the screen is dimmed
        self.assertIn("c_white, 1);\nsurface_reset_target", t["gml_Object_obj_heart_slime_Draw_0"])
        self.assertIn("c_gray, (1) * dronline_soul_alpha_controls(0));", t["gml_Object_obj_heart_slime_Draw_0"])
        self.assertIn("image_blend, (image_alpha) * dronline_soul_alpha_controls(0));\n"
                      "dronline_draw_self(dronline_soul_alpha_controls(0));", t["gml_Object_obj_orangeheart_Draw_0"])
        self.assertIn("obj_grazebox 1", note)
        self.assertIn("obj_orangeheart 2", note)

    def test_purple_only_inside_with_obj_heart(self):
        purple = ("draw_sprite_ext(spr_ui, 0, x, y, 1, 1, 0, c_white, 1);\n"
                  "with (obj_heart)\n{\n    if (other.controls == 1)\n"
                  "        draw_sprite_ext(sprite_index, -1, x2 + other.x_shake, y2, 1, 1, 0, c_red, image_alpha);\n}\n"
                  "draw_sprite_ext(spr_ui, 1, x, y, 1, 1, 0, c_white, 1);\n")
        t, note = self.run_on(**{dp.PURPLE_DRAW: purple})
        p = t[dp.PURPLE_DRAW]
        self.assertIn("c_red, (image_alpha) * dronline_heart_alpha(2));", p)
        self.assertEqual(p.count("c_white, 1);"), 2, "the purple SOUL's own drawing is not a heart")
        self.assertIn("purple SOUL 1", note)

    def test_comments_in_and_around_draw_calls(self):
        src = (
            "// draw_sprite_ext(s, 0, x2, y2, 1, 1, 0, c_white, 1);\n"
            "// draw_self();\n"
            "/* draw_sprite_ext(s, 0, x3, y3, 1, 1, 0, c_white, 1); */\n"
            "draw_sprite_ext(s, 0, x2, y2, 1, 1, 0, c_white, 1 // alpha\n"
            ");\n"
            "draw_sprite_ext(s, 0, x3, y3, 1, 1, /* a, ) */ 0, c_white, // color, alpha\n"
            "    image_alpha // end\n"
            "    );\n"
            "draw_sprite_ext(spr_tail, 0, tx // x2 in comment\n"
            "    , ty, 1, 1, 0, c_orange, 0.5);\n"
        )
        t, note = self.run_on(**{dp.HEART_DRAW: src})
        d = t[dp.HEART_DRAW]
        # commented-out calls stay untouched
        self.assertIn("// draw_sprite_ext(s, 0, x2, y2, 1, 1, 0, c_white, 1);\n", d)
        self.assertIn("// draw_self();\n", d)
        self.assertIn("/* draw_sprite_ext(s, 0, x3, y3, 1, 1, 0, c_white, 1); */\n", d)
        # trailing newline after // in alpha is kept so the closing paren and factor are not commented out
        self.assertIn("c_white, (1 // alpha\n) * dronline_heart_alpha(2));\n", d)
        self.assertIn("c_white, (// color, alpha\n    image_alpha // end\n) * dronline_heart_alpha(3));\n", d)
        # comment mentioning x2 does not misclassify a tail draw, and newline after // in xa is preserved
        self.assertIn("(0.5) * dronline_heart_alpha_at(tx // x2 in comment\n, ty));\n", d)
        self.assertEqual(note, "obj_heart 3 draws (1 by the nearest heart, 0 left as they are)")

    def test_missing_entries(self):
        t, note = self.run_on()
        self.assertEqual(t, {})
        self.assertIn("obj_heart has no Draw", note)

    def test_after_the_heart_hooks(self):
        # the Draw wrap of the own SOUL and the alpha rewrite work together, in the order patch() runs them
        t = heart_texts(**{dp.HEART_DRAW: SOUL_DRAW})
        dp.heart_hooks(t)
        dp.soul_alpha(t)
        d = t[dp.HEART_DRAW]
        self.assertTrue(d.startswith("dronline_heart_draw(true);\n"))
        self.assertTrue(d.rstrip().endswith("dronline_heart_draw(false);"))
        self.assertIn("dronline_heart_alpha(3)", d)


if __name__ == "__main__":
    unittest.main()

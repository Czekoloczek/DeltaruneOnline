#!/usr/bin/env python3
"""DeltaruneOnline patcher.

Takes a chapter data.win that the user already has (DELTARUNE + Local Multiplayer mod applied),
decompiles it with UndertaleModTool CLI, rewrites every keyboard/gamepad/mouse read to the dro_* virtual
input functions, hooks the lockstep driver into obj_time, draws the other players' SOULs faint (drawing
only, other_souls in dronline.ini) and compiles the result into a new data.win.
Nothing from the game or the mod is stored in this repository: all edits are rules applied locally.

Usage:
  python3 dronline_patch.py --utmt PATH/UndertaleModCli.dll --data chapter5_windows/data.win --out patched.win
  (--utmt may also point to UndertaleModCli.exe on Windows)
"""
import argparse
import bisect
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MODULE = os.path.join(REPO, "gml", "scr_dronline.gml")
MODULE_NAME = "gml_GlobalScript_scr_dronline"

# Built-in input reads -> virtual layer. Order matters only for readability: each pattern ends in "(".
RULES = [
    (r"\bkeyboard_check_pressed\(", "dro_kb_pressed("),
    (r"\bkeyboard_check_released\(", "dro_kb_released("),
    (r"\bkeyboard_check_direct\(", "dro_kb_check("),
    (r"\bkeyboard_check\(", "dro_kb_check("),
    (r"\bkeyboard_clear\(", "dro_kb_clear("),
    (r"\bio_clear\(\s*\)", "dro_io_clear()"),
    (r"\bgamepad_button_check_pressed\(", "dro_gp_pressed("),
    (r"\bgamepad_button_check_released\(", "dro_gp_released("),
    (r"\bgamepad_button_check\(", "dro_gp_check("),
    (r"\bgamepad_axis_value\(", "dro_gp_axis("),
    (r"\bgamepad_is_connected\(", "dro_gp_connected("),
    (r"\bgamepad_get_device_count\(", "dro_gp_count("),
    # mouse reads: online every machine sees the same off-screen position and no buttons held
    # (obj_attack_blue_flowerbullet explodes a bullet when the cursor is over it). Only functions that DELTARUNE's
    # runner has: a stand-in that calls a missing one (window_mouse_get_delta_x/y) stops the game while it loads
    # ("Unable to find function"), and code the runner can load can't call them either.
    (r"\bmouse_check_button_pressed\(", "dro_mouse_pressed("),
    (r"\bmouse_check_button_released\(", "dro_mouse_released("),
    (r"\bmouse_check_button\(", "dro_mouse_check("),
    (r"\bmouse_wheel_up\(\s*\)", "dro_mouse_wheel_up()"),
    (r"\bmouse_wheel_down\(\s*\)", "dro_mouse_wheel_down()"),
    (r"\bwindow_mouse_get_x\(\s*\)", "dro_window_mouse_x()"),
    (r"\bwindow_mouse_get_y\(\s*\)", "dro_window_mouse_y()"),
    (r"\bdevice_mouse_check_button_pressed\(", "dro_device_mouse_pressed("),
    (r"\bdevice_mouse_check_button_released\(", "dro_device_mouse_released("),
    (r"\bdevice_mouse_check_button\(", "dro_device_mouse_check("),
    (r"\bdevice_mouse_x_to_gui\(", "dro_device_mouse_x_to_gui("),
    (r"\bdevice_mouse_y_to_gui\(", "dro_device_mouse_y_to_gui("),
    (r"\bdevice_mouse_x\(", "dro_device_mouse_x("),
    (r"\bdevice_mouse_y\(", "dro_device_mouse_y("),
    (r"\brandomize\(\s*\)", "dro_randomize()"),
    (r"\brandomise\(\s*\)", "dro_randomize()"),
    # the system clock in microseconds -> a clock that counts lockstep frames (obj_spin_petal's Draw moves the
    # petals with it, so their x differed between machines from the first frame in the Chapter 5 garden)
    (r"\bget_timer\(\s*\)", "dro_get_timer()"),
    # chapter change: every machine leaves in the same frame, so the others' goodbyes are expected
    (r"\bgame_change\(", "dro_game_change("),
    # restart: fires Game End, but the game goes on, so no goodbye then
    (r"\bgame_restart\(\s*\)", "dro_game_restart()"),
    # Local Multiplayer player count: online it comes from the session, not the local keyconfig_mod.ini
    (r"\bini_read_real\(\s*\"PLAYER_AMOUNT\"", "dro_ini_player_amount(\"PLAYER_AMOUNT\""),
    # audio state that game logic reads -> virtual audio clock
    (r"\baudio_play_sound_on\(", "dro_audio_play_sound_on("),
    (r"\baudio_play_sound\(", "dro_audio_play_sound("),
    (r"\baudio_sound_get_track_position\(", "dro_audio_get_pos("),
    (r"\baudio_sound_set_track_position\(", "dro_audio_set_pos("),
    (r"\baudio_sound_pitch\(", "dro_audio_pitch("),
    (r"\baudio_is_playing\(", "dro_audio_is_playing("),
    (r"\baudio_is_paused\(", "dro_audio_is_paused("),
    (r"\baudio_pause_sound\(", "dro_audio_pause("),
    (r"\baudio_resume_sound\(", "dro_audio_resume("),
    (r"\baudio_stop_sound\(", "dro_audio_stop("),
    (r"\baudio_stop_all\(\s*\)", "dro_audio_stop_all()"),
    (r"\baudio_pause_all\(\s*\)", "dro_audio_pause_all()"),
    (r"\baudio_resume_all\(\s*\)", "dro_audio_resume_all()"),
]
RULES = [(re.compile(a), b) for a, b in RULES]

# Built-in read-only variables rewritten outside strings, comments, field access and assignments: online,
# mouse_x/mouse_y give the same off-screen cursor on every PC, and current_time (ms) and delta_time (us) use the
# lockstep frame clock (docs/DESIGN.md, "System clock").
# The assignment check skips whitespace and comments: `mouse_x /* note */ = 0` stays as it is. Inside that check
# a comment must match as a whole (a block comment ends at its first */, a line comment at the end of its line),
# or the lookahead could stop inside one comment or run on into the next and find an `=` that is not code.
_WHOLE_COMMENT = r'(?://[^\n]*(?![^\n])|/\*(?:[^*]|\*(?!/))*\*/)'
VAR_RULES = {
    "mouse_x": "dro_mouse_x()",
    "mouse_y": "dro_mouse_y()",
    "current_time": "dro_current_time()",
    "delta_time": "dro_delta_time()",
}
VAR_TOKEN_RX = re.compile(
    r'@"[^"]*"|@\'[^\']*\'|"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|(?<![.\w])(' + "|".join(VAR_RULES) + r')\b'
    r'(?!(?:\s|' + _WHOLE_COMMENT + r')*(?:\(|(?:\+\+|--)|(?:[+\-*/%&|^]|<<|>>)?=(?!=)))',
    re.S,
)

HOOK_STEP = "gml_Object_obj_time_Step_1"      # Begin Step: input arrays are computed here
HOOK_END = "gml_Object_obj_time_Other_3"      # Game End
HOOK_GUI = "gml_Object_obj_time_Draw_64"       # Draw GUI: status line
DLL = os.path.join(REPO, "native", "build", "drnet.dll")

# Own SOUL (see "own SOUL" in scr_dronline.gml): hits on a heart are decided by its owner while the
# owner's input is delayed. Heart 1 is hit through obj_heart's collision event, hearts 2 and 3 through
# instance_place calls in its Step.
HEART_COLL = "gml_Object_obj_heart_Collision_obj_collidebullet"
HEART_STEP = "gml_Object_obj_heart_Step_0"
HEART_END = "gml_Object_obj_heart_Step_2"
HEART_DRAW = "gml_Object_obj_heart_Draw_0"
PLACE_RX = re.compile(r"\binstance_place\(\s*x([23])\s*,\s*y\1\s*,\s*obj_collidebullet\s*\)")
HEART_OFF = """
// ---- generated by the patcher: obj_heart hooks not applied (unknown code), own SOUL hits off ----
function dro_heart_hooked()
{
    return false;
}

function dro_heart_collide_p1(_dro_b)
{
}
"""


def heart_hooks(texts):
    """Rewrites the obj_heart entries in `texts` (name -> code) and returns GML to append to the module.
    Everything or nothing: if any entry looks different from what the hooks expect, nothing changes."""
    if not all(n in texts for n in (HEART_COLL, HEART_STEP, HEART_END)):
        return HEART_OFF, "obj_heart entries missing"
    # Own hits need the own heart drawn ahead, so that a hit lands where its owner sees the heart. An `exit` or
    # `return` in Draw would skip the call that puts the shared position back, so such a Draw can't be wrapped,
    # and without the wrap the hooks stay off (DESIGN: every hit follows what the dodging player saw).
    draw = texts.get(HEART_DRAW)
    if draw is None:
        return HEART_OFF, "no obj_heart Draw event"
    if re.search(r"\b(exit|return)\b", draw):
        return HEART_OFF, "obj_heart Draw contains exit or return"
    body = texts[HEART_COLL]
    withs = re.findall(r"\bwith\s*\(([^)]*)\)", body)
    if any(w.strip() != "other" for w in withs) or re.search(r"\bfunction\b", body):
        return HEART_OFF, "unexpected collision event"
    step, n = PLACE_RX.subn(lambda m: f"dronline_place({m.group(1)}, x{m.group(1)}, y{m.group(1)})", texts[HEART_STEP])
    if n == 0:
        return HEART_OFF, "no hit checks for hearts 2/3 in obj_heart Step"
    texts[HEART_STEP] = step
    texts[HEART_COLL] = "if (!dronline_ownhit(1))\n    dro_heart_collide_p1(other);\n"
    texts[HEART_END] = "dronline_heart_endstep();\n" + texts[HEART_END]
    texts[HEART_DRAW] = "dronline_heart_draw(true);\n" + draw + "\ndronline_heart_draw(false);\n"
    note = f"{n} hit checks for hearts 2/3, own heart drawn ahead"
    fn = re.sub(r"\bother\b", "_dro_b", body)
    gen = ("\n// ---- generated by the patcher from obj_heart's collision event with obj_collidebullet ----\n"
           "function dro_heart_hooked()\n{\n    return true;\n}\n\n"
           "function dro_heart_collide_p1(_dro_b)\n{\n" + fn.rstrip() + "\n}\n")
    return gen, note


# Other players' SOULs drawn faint (other_souls in dronline.ini, see "other players' SOULs" in scr_dronline.gml).
# Drawing only: the alpha argument of every call that draws a SOUL is multiplied by a factor that is 1 offline, in
# solo and for the local player's own heart. No statement is added, removed or reordered, so the game state stays
# the same on every machine (obj_heart's Draw advances image_index2/3 itself, obj_grazebox's counts its timer).
ALPHA_ARG = {"draw_sprite_ext": (2, 3, 8), "draw_sprite_part_ext": (6, 7, 11), "draw_surface_ext": (1, 2, 7)}
SPRITES = ("draw_sprite_ext", "draw_sprite_part_ext")
DRAW_SELF_RX = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|(\bdraw_self\(\s*\))', re.S)
DRAW_SELF = "dronline_draw_self({})"
WITH_HEART_RX = re.compile(r"\bwith\s*\(\s*obj_heart\s*\)\s*\{")
# Objects that draw one player's SOUL, with the instance variable that says whose (1-based or 0-based) and the
# draw functions that put it on the screen. obj_heart_slime draws into a surface first: only the surface is dimmed.
SOUL_OWNERS = {
    "gml_Object_obj_grazebox_Draw_0": ("dronline_soul_alpha_controls(1)", SPRITES),
    "gml_Object_obj_orangeheart_Draw_0": ("dronline_soul_alpha_controls(0)", SPRITES),
    "gml_Object_obj_date_heart_Draw_0": ("dronline_soul_alpha_controls(0)", SPRITES),
    "gml_Object_obj_heart_slime_Draw_0": ("dronline_soul_alpha_controls(0)", ("draw_surface_ext",)),
}
PURPLE_DRAW = "gml_Object_obj_purplecontrols_Draw_0"
CALL_IN_RX = re.compile(r"\b[A-Za-z_]\w*\s*\(")
WRITE_IN_RX = re.compile(r"\+\+|--|(?<![=!<>])=(?!=)")
COMMENT_RX = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
DRAW_CALL_TOKEN_RX = re.compile(
    r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|\b(draw_sprite_ext|draw_sprite_part_ext|draw_surface_ext)\s*\(',
    re.S,
)


def _skip_ignored(text, i, end):
    """If a string or comment starts at text[i], returns the index just past it (at most end); else -1."""
    if text[i] == '"':
        i += 1
        while i < end and text[i] != '"':
            i += 2 if text[i] == "\\" else 1
        return min(i + 1, end)
    if text.startswith("//", i):
        nl = text.find("\n", i + 2, end)
        return end if nl < 0 else nl + 1
    if text.startswith("/*", i):
        stop = text.find("*/", i + 2, end)
        return end if stop < 0 else stop + 2
    return -1


def _close(text, i):
    """Index of the bracket that closes the one opening at text[i] (strings and comments skipped), or -1."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = []
    n = len(text)
    while i < n:
        skip = _skip_ignored(text, i, n)
        if skip > 0:
            i = skip
            continue
        c = text[i]
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")]}":
            if not stack or stack.pop() != c:
                return -1
            if not stack:
                return i
        i += 1
    return -1


def draw_calls(text, names, lo=0, hi=None):
    """The top-level calls of the functions in `names` in text[lo:hi]: (start, end, name, [(arg start, arg end)])."""
    hi = len(text) if hi is None else hi
    out = []
    pos = lo
    while True:
        m = DRAW_CALL_TOKEN_RX.search(text, pos, hi)
        if not m:
            return out
        if not m.group(1) or m.group(1) not in names:
            pos = m.end()
            continue
        op = m.end() - 1
        end = _close(text, op)
        if end < 0 or end >= hi:
            pos = m.end()
            continue
        args = []
        a = i = op + 1
        while 0 < i < end:
            skip = _skip_ignored(text, i, end)
            if skip > 0:
                i = skip
                continue
            c = text[i]
            if c in "([{":
                i = _close(text, i)
                if i < 0:
                    break
            elif c == ",":
                args.append((a, i))
                a = i + 1
            i += 1
        if i == end:
            args.append((a, end))
            out.append((m.start(), end + 1, m.group(1), args))
        pos = end + 1


def heart_factor(xa, ya):
    """Factor for a draw in obj_heart at (xa, ya): the heart its position names (x/y, x2/y2 or x3/y3), else the
    heart nearest to that point, or None when the position can't be evaluated a second time without effects."""
    clean = COMMENT_RX.sub(" ", xa + " " + ya)
    ids = set(re.findall(r"(?<![\w.])[A-Za-z_]\w*", clean))
    hearts = {k for k, names in ((1, {"x", "y"}), (2, {"x2", "y2"}), (3, {"x3", "y3"})) if ids & names}
    if len(hearts) == 1:
        return f"dronline_heart_alpha({hearts.pop()})", False
    if hearts or CALL_IN_RX.search(clean) or WRITE_IN_RX.search(clean):
        return None, False
    return f"dronline_heart_alpha_at({xa.strip(' \t')}, {ya.strip(' \t')})", True


def dim_calls(text, factor, names, lo=0, hi=None):
    """Multiplies the alpha argument of each call by factor(x arg, y arg) -> (GML or None, by nearest).
    Returns the new text and the counts (dimmed, of them by the nearest heart, left as they are)."""
    calls = draw_calls(text, names, lo, hi)
    done = near = left = 0
    for _start, _end, name, args in reversed(calls):
        xi, yi, ai = ALPHA_ARG[name]
        if len(args) != ai + 1:
            left += 1
            continue
        f, by_near = factor(text[slice(*args[xi])], text[slice(*args[yi])])
        if f is None:
            left += 1
            continue
        a0, a1 = args[ai]
        alpha = text[a0:a1].strip(" \t")
        text = text[:a0] + (" " if text[a0:a0 + 1] == " " else "") + f"({alpha}) * {f}" + text[a1:]
        done += 1
        near += by_near
    return text, (done, near, left)


def soul_alpha(texts):
    """Rewrites the SOUL draw calls in `texts` (name -> code) so other players' SOULs are drawn with the
    other_souls factor. Returns a note for the build output."""
    notes = []

    def dim(code, factor, names, lo=0, hi=None, self_factor=None):
        code, (done, near, left) = dim_calls(code, factor, names, lo, hi)
        if self_factor is not None:
            def _sub_self(m):
                nonlocal done
                if not m.group(1):
                    return m.group(0)
                done += 1
                return DRAW_SELF.format(self_factor)
            code = DRAW_SELF_RX.sub(_sub_self, code)
        return code, done, near, left

    draw = texts.get(HEART_DRAW)
    if draw is None:
        notes.append("obj_heart has no Draw")
    else:
        texts[HEART_DRAW], done, near, left = dim(draw, heart_factor, SPRITES, self_factor="dronline_heart_alpha(1)")
        notes.append(f"obj_heart {done} draws ({near} by the nearest heart, {left} left as they are)")
    purple = texts.get(PURPLE_DRAW)
    if purple is not None:
        total = 0
        pos = 0
        while True:
            m = WITH_HEART_RX.search(purple, pos)
            end = _close(purple, m.end() - 1) if m else -1
            if end < 0:
                break
            purple, done, _, _ = dim(purple, heart_factor, SPRITES, m.end(), end)
            total += done
            pos = _close(purple, m.end() - 1) + 1
        texts[PURPLE_DRAW] = purple
        notes.append(f"purple SOUL {total}")
    for name, (factor, names) in SOUL_OWNERS.items():
        code = texts.get(name)
        if code is not None:
            texts[name], done, _, _ = dim(code, lambda _xa, _ya, f=factor: (f, False), names,
                                          self_factor=factor if "draw_sprite_ext" in names else None)
            notes.append(f"{name[len('gml_Object_'):-len('_Draw_0')]} {done}")
    return ", ".join(notes)


def run_utmt(utmt, data, scripts, out=None, env_extra=None):
    if isinstance(scripts, str):
        scripts = [scripts]
    cmd = (["dotnet", utmt] if utmt.lower().endswith(".dll") else [utmt]) + ["load", data] + [x for s in scripts for x in ("-s", s)]
    if out:
        cmd += ["-o", out]
    env = dict(os.environ, **(env_extra or {}))
    # stdin must be closed: the CLI waits for interactive input after the script otherwise
    res = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if res.returncode != 0 or "Script execution failed" in res.stdout + res.stderr:
        sys.stderr.write(res.stdout[-4000:] + res.stderr[-4000:])
        raise SystemExit("UndertaleModTool failed")
    return res.stdout


def export_check(out):
    """Stops if export_code.csx could not decompile every entry. An entry that is left out is never rewritten,
    so it keeps reading this machine's own keyboard and audio, and the games go out of sync without a word."""
    m = re.search(r"EXPORT_OK (\d+) entries, failed (\d+)", out)
    if not m:
        sys.stderr.write(out[-4000:])
        raise SystemExit("UndertaleModTool did not report the decompiled entries")
    if int(m.group(2)):
        sys.stderr.write("".join(l + "\n" for l in out.splitlines() if l.startswith("DECOMPILE_FAIL")))
        raise SystemExit(f"{m.group(2)} of {m.group(1)} code entries could not be decompiled: "
                         "a patched game would read its own keyboard there and go out of sync")
    return int(m.group(1))


SKIP_RX = re.compile(r'@"[^"]*"|@\'[^\']*\'|"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)


def _preceded_by_dot(text, pos, comment_spans, comment_ends):
    """True when the nearest non-whitespace, non-comment character before pos is '.'."""
    i = pos - 1
    while i >= 0:
        if text[i].isspace():
            i -= 1
            continue
        k = bisect.bisect_right(comment_ends, i)
        if k < len(comment_spans) and comment_spans[k][0] <= i < comment_spans[k][1]:
            i = comment_spans[k][0] - 1
            continue
        return text[i] == "."
    return False


def _rewrite_rule(rx, repl, text):
    spans = []
    comment_spans = []
    comment_ends = []
    for m in SKIP_RX.finditer(text):
        span = (m.start(), m.end())
        spans.append(span)
        if text[span[0]] == "/":
            comment_spans.append(span)
            comment_ends.append(span[1])
    si = 0
    count = 0

    def _sub(m):
        nonlocal si, count
        pos = m.start()
        while si < len(spans) and spans[si][1] <= pos:
            si += 1
        if si < len(spans) and spans[si][0] <= pos:
            return m.group(0)
        if _preceded_by_dot(text, pos, comment_spans, comment_ends):
            return m.group(0)
        count += 1
        return repl

    return rx.sub(_sub, text), count


def rewrite(text):
    total = 0
    for rx, repl in RULES:
        text, n = _rewrite_rule(rx, repl, text)
        total += n

    comment_spans = []
    comment_ends = []
    for m in SKIP_RX.finditer(text):
        if text[m.start()] == "/":
            comment_spans.append((m.start(), m.end()))
            comment_ends.append(m.end())

    def _sub_var(m):
        nonlocal total
        name = m.group(1)
        if not name or _preceded_by_dot(text, m.start(1), comment_spans, comment_ends):
            return m.group(0)
        total += 1
        return VAR_RULES[name]

    text = VAR_TOKEN_RX.sub(_sub_var, text)
    return text, total


def patch(a, work):
    """Steps 1 to 4 in the work folder."""
    code_dir = os.path.join(work, "code")
    imp_dir = os.path.join(work, "import")
    for d in (code_dir, imp_dir):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)

    print("1/4 decompiling", a.data)
    export_check(run_utmt(a.utmt, a.data, os.path.join(HERE, "utmt", "export_code.csx"), env_extra={"UTMT_OUT": code_dir}))
    names = {f[:-4] for f in os.listdir(code_dir) if f.endswith(".gml")}
    if MODULE_NAME in names:
        raise SystemExit("this data.win is already patched; start from the unpatched file")
    if HOOK_STEP not in names:
        raise SystemExit(HOOK_STEP + " not found: is this a DELTARUNE chapter data.win?")

    print("2/4 rewriting input reads")
    calls = files = 0
    texts = {}
    srcs = {}
    for name in sorted(names):
        path = os.path.join(code_dir, name + ".gml")
        src = open(path, encoding="utf-8").read()
        new, n = rewrite(src)
        if name == HOOK_STEP:
            new = "dronline_step();\n" + new
        if name == HOOK_GUI:
            new = new + "\ndronline_draw();\n"
        srcs[name] = src
        texts[name] = new
        calls += n
    heart_gml, heart_note = heart_hooks(texts)
    soul_note = soul_alpha(texts)
    for name, new in texts.items():
        if new != srcs[name]:
            open(os.path.join(imp_dir, name + ".gml"), "w", encoding="utf-8").write(new)
            files += 1
    end_src = open(os.path.join(code_dir, HOOK_END + ".gml"), encoding="utf-8").read() if HOOK_END in names else ""
    open(os.path.join(imp_dir, HOOK_END + ".gml"), "w", encoding="utf-8").write("dronline_shutdown();\n" + rewrite(end_src)[0])
    module = open(MODULE, encoding="utf-8").read().rstrip("\n") + "\n" + heart_gml
    open(os.path.join(imp_dir, MODULE_NAME + ".gml"), "w", encoding="utf-8").write(module)
    print(f"    {calls} calls in {files} code entries, + obj_time hooks, + {MODULE_NAME}")
    print(f"    own SOUL hooks: {heart_note}")
    print(f"    other players' SOULs drawn faint: {soul_note}")

    print("3/4 compiling")
    out = run_utmt(a.utmt, a.data, [os.path.join(HERE, "utmt", "add_extension.csx"), os.path.join(HERE, "utmt", "import_code.csx")],
                   out=a.out, env_extra={"UTMT_IN": imp_dir})
    for key in ("EXT_OK", "IMPORT_OK"):
        print("    " + next((l for l in out.splitlines() if key in l), key + "?"))
    dll_out = os.path.join(os.path.dirname(os.path.abspath(a.out)), "drnet.dll")
    if os.path.abspath(a.dll) != dll_out:
        shutil.copy(a.dll, dll_out)
    print("    copied drnet.dll next to the output (it must sit next to data.win)")

    print("4/4 done:", a.out)
    print("    put dronline.ini (see config/dronline.ini.example) into the save folder to enable a mode")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--utmt", required=True, help="UndertaleModCli.dll (run with dotnet) or UndertaleModCli.exe")
    ap.add_argument("--data", required=True, help="input data.win (game + Local Multiplayer mod)")
    ap.add_argument("--out", required=True, help="output data.win")
    ap.add_argument("--work", help="work folder (default: temporary)")
    ap.add_argument("--keep", action="store_true", help="keep the work folder")
    ap.add_argument("--dll", default=DLL, help="drnet.dll to copy next to the output (default: native/build/drnet.dll)")
    a = ap.parse_args()
    if not os.path.isfile(a.dll):
        raise SystemExit("drnet.dll not found: build it with native/build.sh or pass --dll")

    work = a.work or tempfile.mkdtemp(prefix="dronline_")
    try:
        patch(a, work)
    finally:
        # also on an error: the temporary folder holds the decompiled chapter
        if not (a.keep or a.work):
            shutil.rmtree(work, ignore_errors=True)
        else:
            print("    work folder:", work)


if __name__ == "__main__":
    main()

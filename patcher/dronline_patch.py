#!/usr/bin/env python3
"""DeltaruneOnline patcher.

Takes a chapter data.win that the user already has (DELTARUNE + Local Multiplayer mod applied),
decompiles it with UndertaleModTool CLI, rewrites every keyboard/gamepad/mouse read to the dro_* virtual
input functions, hooks the lockstep driver into obj_time (plus the own SOUL hooks in obj_heart and the immortal SOUL
guards in the damage scripts, both used only when dronline.ini asks) and compiles the result into a new data.win.
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

# Built-in read-only variables rewritten outside strings, comments, field access and assignments.
# The assignment check skips whitespace and comments: `mouse_x /* note */ = 0` stays as it is. Inside that check
# a comment must match as a whole (a block comment ends at its first */, a line comment at the end of its line),
# or the lookahead could stop inside one comment or run on into the next and find an `=` that is not code.
_WHOLE_COMMENT = r'(?://[^\n]*(?![^\n])|/\*(?:[^*]|\*(?!/))*\*/)'
VAR_RULES = {
    "mouse_x": "dro_mouse_x()",
    "mouse_y": "dro_mouse_y()",
}
VAR_TOKEN_RX = re.compile(
    r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|(?<![.\w])(mouse_x|mouse_y)\b'
    r'(?!(?:\s|' + _WHOLE_COMMENT + r')*(?:\(|(?:[+\-*/%&|^]|<<|>>)?=(?!=)))',
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


# Immortal SOUL (immortal= in dronline.ini, see "immortal SOUL" in scr_dronline.gml): a test aid. Chapter 5's damage
# scripts all lower global.hp and end with the game-over check (no party member above 0 HP -> scr_gameover); the
# bullets reach them through obj_collidebullet's User Event 5. While immortal, each of them returns at once, so HP
# never drops, and scr_gameover heals the party and returns instead, for the few places that call it directly.
IMMORTAL_DAMAGE = ("scr_damage", "scr_damage_all", "scr_damage_proportional", "scr_damage_sneo_final_attack",
                   "scr_damage_all_overworld", "scr_damage_all_platmode")
IMMORTAL_NEEDED = ("scr_damage", "scr_gameover")
IMMORTAL_OFF = """
// ---- generated by the patcher: immortal SOUL hooks not applied (unknown code) ----
function dro_immortal_hooked()
{
    return false;
}
"""


def _function_start(name):
    """The start of `function name(...)` up to and including its `{` (the decompiler may put a // comment after
    the argument list)."""
    return re.compile(r"\bfunction\s+" + name + r"\s*\((?:[^()]|\([^()]*\))*\)[ \t]*(?://[^\n]*)?\s*\{")


def immortal_hooks(texts):
    """Puts the immortal SOUL guards into the damage scripts and scr_gameover in `texts` (name -> code) and returns
    GML to append to the module. Everything or nothing: if a script is missing or looks different, nothing changes."""
    guards = {}
    for fn in IMMORTAL_DAMAGE + ("scr_gameover",):
        entry = "gml_GlobalScript_" + fn
        if entry not in texts:
            if fn in IMMORTAL_NEEDED:
                return IMMORTAL_OFF, entry + " missing"
            continue
        m = list(_function_start(fn).finditer(texts[entry]))
        if len(m) != 1:
            return IMMORTAL_OFF, "unexpected " + entry
        call = "dro_immortal_gameover()" if fn == "scr_gameover" else "dro_immortal()"
        guards[entry] = (m[0].end(), "\n    if (" + call + ")\n        return;")
    for entry, (pos, guard) in guards.items():
        texts[entry] = texts[entry][:pos] + guard + texts[entry][pos:]
    gen = ("\n// ---- generated by the patcher: immortal SOUL guards are in "
           + ", ".join(sorted(e[len("gml_GlobalScript_"):] for e in guards)) + " ----\n"
           "function dro_immortal_hooked()\n{\n    return true;\n}\n")
    return gen, f"guards in {len(guards) - 1} damage scripts and scr_gameover"


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


SKIP_RX = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)


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
    immortal_gml, immortal_note = immortal_hooks(texts)
    for name, new in texts.items():
        if new != srcs[name]:
            open(os.path.join(imp_dir, name + ".gml"), "w", encoding="utf-8").write(new)
            files += 1
    end_src = open(os.path.join(code_dir, HOOK_END + ".gml"), encoding="utf-8").read() if HOOK_END in names else ""
    open(os.path.join(imp_dir, HOOK_END + ".gml"), "w", encoding="utf-8").write("dronline_shutdown();\n" + rewrite(end_src)[0])
    module = open(MODULE, encoding="utf-8").read().rstrip("\n") + "\n" + heart_gml + immortal_gml
    open(os.path.join(imp_dir, MODULE_NAME + ".gml"), "w", encoding="utf-8").write(module)
    print(f"    {calls} calls in {files} code entries, + obj_time hooks, + {MODULE_NAME}")
    print(f"    own SOUL hooks: {heart_note}")
    print(f"    immortal SOUL hooks: {immortal_note}")

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

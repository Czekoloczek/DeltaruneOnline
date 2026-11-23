#!/usr/bin/env python3
"""Input scripts for unattended tests: text <-> the game's per-frame input file.

The game (script= in dronline.ini) reads one little-endian u16 of action bits per input frame, the same format
script_record= writes and mode=record writes to dronline_input.bin. This tool turns a text script into that file
and back.

  python3 tools/input_script.py compile <script.txt> <out.bin>
  python3 tools/input_script.py dump <in.bin> [<out.txt>]      (a recording as text; compiles back to the same bytes)
  python3 tools/input_script.py info <script.txt | in.bin>     (frames, length, buttons used)

Text script, one step per line, '#' starts a comment:

  <frames> <buttons>            hold <buttons> for <frames> input frames ('-' = nothing held)
  wait <frames>                 same as '<frames> -'
  tap <buttons> [times] [gap]   press for 1 frame, then nothing for <gap> frames (defaults: 1 time, gap 1)

<frames> and <gap> are frame counts (30 per second) or seconds with an 's', like 1.5s (45 frames).
<buttons> are joined with '+': down right up left confirm cancel menu (or d r u l z x c, the default keys),
and the mod's teleport f1 f2 f3. Example:

  wait 3s           # the intro
  tap confirm 2 15  # two presses, half a second apart
  20 right          # walk right for 20 frames
  10 up+cancel      # run up

Online, an input frame is an exchanged frame: waiting for the others does not use up the script. With delay=auto
the game picks the input delay from the ping, so set the same delay=<frames> on every machine when a replay must
match a recording frame for frame.
"""
import struct
import sys

FPS = 30
# Bit layout of the action bits: gml/scr_dronline.gml, dronline_local_bits (bit 11 and up belong to the network).
BUTTONS = ["down", "right", "up", "left", "confirm", "cancel", "menu", "teleport", "f1", "f2", "f3"]
ALIASES = {"d": "down", "r": "right", "u": "up", "l": "left", "z": "confirm", "x": "cancel", "c": "menu"}
MAX_BITS = (1 << len(BUTTONS)) - 1


class ScriptError(ValueError):
    pass


def parse_frames(word, what="frames", allow_zero=False):
    w = word.lower()
    try:
        n = round(float(w[:-1]) * FPS) if w.endswith("s") else int(w)
    except ValueError:
        raise ScriptError("%s must be a number of frames or seconds like 1.5s, not %r" % (what, word))
    if n < 0 or (n == 0 and not allow_zero):
        raise ScriptError("%s must be %s, not %r" % (what, "0 or more" if allow_zero else "more than 0", word))
    return n


def parse_buttons(word):
    if word == "-":
        return 0
    bits = 0
    for name in word.lower().split("+"):
        name = ALIASES.get(name, name)
        if name not in BUTTONS:
            raise ScriptError("unknown button %r (use %s, or d r u l z x c)" % (name, " ".join(BUTTONS)))
        bits |= 1 << BUTTONS.index(name)
    return bits


def button_names(bits):
    if bits == 0:
        return "-"
    return "+".join(name for i, name in enumerate(BUTTONS) if bits & (1 << i))


def compile_text(text):
    """Returns the list of per-frame bits."""
    frames = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        words = raw.split("#", 1)[0].split()
        if not words:
            continue
        try:
            if words[0].lower() == "wait":
                if len(words) != 2:
                    raise ScriptError("wait takes one number: wait <frames>")
                frames += [0] * parse_frames(words[1])
            elif words[0].lower() == "tap":
                if not 2 <= len(words) <= 4:
                    raise ScriptError("tap <buttons> [times] [gap]")
                bits = parse_buttons(words[1])
                times = parse_frames(words[2], "times") if len(words) > 2 else 1
                gap = parse_frames(words[3], "gap", allow_zero=True) if len(words) > 3 else 1
                frames += ([bits] + [0] * gap) * times
            else:
                if len(words) != 2:
                    raise ScriptError("expected '<frames> <buttons>', 'wait <frames>' or 'tap <buttons> [times] [gap]'")
                frames += [parse_buttons(words[1])] * parse_frames(words[0])
        except ScriptError as e:
            raise ScriptError("line %d: %s" % (lineno, e))
    return frames


def to_bytes(frames):
    return b"".join(struct.pack("<H", b) for b in frames)


def from_bytes(data):
    if len(data) % 2:
        raise ScriptError("the file has an odd number of bytes, so it is not an input file")
    frames = [v for (v,) in struct.iter_unpack("<H", data)]
    bad = next((i for i, b in enumerate(frames) if b > MAX_BITS), None)
    if bad is not None:
        raise ScriptError("frame %d has bits %d, more than the %d buttons" % (bad, frames[bad], len(BUTTONS)))
    return frames


def dump_text(frames):
    lines, i = [], 0
    while i < len(frames):
        j = i
        while j < len(frames) and frames[j] == frames[i]:
            j += 1
        lines.append("%d %s" % (j - i, button_names(frames[i])))
        i = j
    return "".join(line + "\n" for line in lines)


def read(path, mode="rb"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def write(path, data):
    with open(path, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as f:
        f.write(data)


def load(path):
    data = read(path)
    if path.lower().endswith(".bin"):
        return from_bytes(data)
    return compile_text(data.decode("utf-8"))


def main(argv):
    if len(argv) == 3 and argv[0] == "compile":
        frames = compile_text(read(argv[1], "r"))
        write(argv[2], to_bytes(frames))
        print("%s: %d frames (%.1f s)" % (argv[2], len(frames), len(frames) / FPS))
    elif len(argv) in (2, 3) and argv[0] == "dump":
        text = dump_text(from_bytes(read(argv[1])))
        if len(argv) == 3:
            write(argv[2], text)
        else:
            sys.stdout.write(text)
    elif len(argv) == 2 and argv[0] == "info":
        frames = load(argv[1])
        used = 0
        for b in frames:
            used |= b
        print("%d frames (%.1f s), %d with buttons, buttons used: %s" % (
            len(frames), len(frames) / FPS, sum(1 for b in frames if b), button_names(used).replace("+", " ")))
    else:
        sys.stderr.write(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ScriptError as e:
        sys.stderr.write("error: %s\n" % e)
        sys.exit(1)

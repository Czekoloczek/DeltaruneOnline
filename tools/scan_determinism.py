#!/usr/bin/env python3
"""List code that can make two games drift apart (lockstep hazards).
Usage: python3 scan_determinism.py <folder with decompiled .gml files>
Run the patcher with --keep to get the folder (work/code)."""
import os
import re
import sys
from collections import defaultdict

HAZARDS = {
    "audio state": r"\b(audio_is_playing|audio_sound_get_track_position|audio_sound_length|audio_is_paused)\(",
    "system clock": r"\b(current_time|date_current_datetime|get_timer)\b",
    "random": r"\b(random|irandom|random_range|irandom_range|choose)\(",
    "window/display": r"\b(window_get_\w+|display_get_\w+|window_has_focus)\(",
    "file reads": r"\b(file_text_open_read|ini_open|buffer_load|file_exists)\(",
    "fps/delta": r"\b(fps|fps_real|delta_time)\b",
}
folder = sys.argv[1]
hits = defaultdict(list)
for f in sorted(os.listdir(folder)):
    if not f.endswith(".gml"):
        continue
    text = open(os.path.join(folder, f), encoding="utf-8", errors="replace").read()
    for name, rx in HAZARDS.items():
        n = len(re.findall(rx, text))
        if n:
            hits[name].append((n, f[:-4]))
for name in HAZARDS:
    rows = sorted(hits[name], reverse=True)
    print(f"== {name}: {sum(n for n, _ in rows)} uses in {len(rows)} entries")
    for n, f in rows[:15]:
        print(f"   {n:4d}  {f}")

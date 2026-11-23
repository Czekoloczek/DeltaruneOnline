#!/usr/bin/env python3
"""TEMPORARY (removed again before review): asks DeepSeek V4 Pro to draft the agent API from
docs/agent-api-spec.md and writes its raw answer to .github/deepseek-draft/. Run by
.github/workflows/tmp-deepseek-agent-draft.yml, the only place DEEPSEEK_API_KEY exists. Standard library only."""
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, ".github", "deepseek-draft")
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-pro"
REASONING = "high"
MAX_TOKENS = 200_000          # thinking counts as output: a 64K cap once went entirely to thinking
DEADLINE = 95 * 60            # seconds; the job allows 110 minutes

SPEC = "docs/agent-api-spec.md"
CONTEXT = [
    "CLAUDE.md",
    "README.md",
    "docs/DESIGN.md",
    "native/drnet.c",
    "native/build.sh",
    "gml/scr_dronline.gml",
    "patcher/dronline_patch.py",
    "patcher/utmt/add_extension.csx",
    "patcher/utmt/import_code.csx",
    "config/dronline.ini.example",
    "tests/test_consistency.py",
    "tools/input_script.py",
]

SYSTEM = """You are an experienced C (Win32, Winsock, MinGW) and GameMaker Language engineer. You write a first
draft of a new feature for the DeltaruneOnline repository: an online lockstep mod for DELTARUNE (Chapter 5),
built on the Local Multiplayer mod. The repository's current files and the feature spec follow. Read the
existing code carefully and follow its conventions, its comments' style and the pitfalls it documents.

Answer in exactly this format, and nothing else:

## Notes
At most 40 lines: what you built, your design decisions (including the ones the spec left to you), what you are
unsure about, and anything in the spec you did not follow and why.

Then every file you create or change, COMPLETE, each one as:

<<<FILE relative/path/from/repo/root>>>
the whole file content
<<<END FILE>>>

Rules for the files:
- Give the complete content of every file you output. Never elide, abbreviate or write "unchanged", "...",
  "rest as before" or similar: the files are written to disk exactly as you give them.
- No Markdown code fences inside the markers.
- Keep existing code exactly as it is, apart from the lines the feature needs.
- Only paths under native/, gml/, patcher/, tools/, tests/, docs/, config/, and README.md. Do not output
  docs/agent-api-spec.md or files you do not change.
- Never add game or mod files or decompiled game code."""


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def user_message():
    parts = ["# The spec\n\n" + read(SPEC), "# The repository's current files"]
    for p in CONTEXT:
        parts.append(f"----- BEGIN {p} -----\n{read(p).rstrip()}\n----- END {p} -----")
    parts.append("Now write the draft in the required format.")
    return "\n\n".join(parts)


def main():
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        sys.exit("DEEPSEEK_API_KEY is not set")
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_message()}],
        "thinking": {"type": "enabled"},
        "reasoning_effort": REASONING,
        "max_tokens": MAX_TOKENS,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    print(f"request: {MODEL}, reasoning {REASONING}, max_tokens {MAX_TOKENS}, "
          f"prompt {len(body['messages'][1]['content'])} chars", flush=True)
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "text/event-stream",
        "User-Agent": "dronline-agent-draft"})
    content, reasoning, usage, finish, model = [], [], None, None, None
    start = last = time.monotonic()
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            model = chunk.get("model") or model
            usage = chunk.get("usage") or usage
            for ch in chunk.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("reasoning_content"):
                    reasoning.append(d["reasoning_content"])
                if d.get("content"):
                    content.append(d["content"])
                finish = ch.get("finish_reason") or finish
            now = time.monotonic()
            if now - last > 60:
                last = now
                print(f"{int(now - start)} s: thinking {sum(map(len, reasoning))} chars, "
                      f"answer {sum(map(len, content))} chars", flush=True)
            if now - start > DEADLINE:
                print("deadline reached, stopping the stream", flush=True)
                finish = finish or "deadline"
                break
    answer, thought = "".join(content), "".join(reasoning)
    meta = {"model": model, "requested_model": MODEL, "reasoning_effort": REASONING, "max_tokens": MAX_TOKENS,
            "finish_reason": finish, "usage": usage, "seconds": int(time.monotonic() - start),
            "answer_chars": len(answer), "reasoning_chars": len(thought)}
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "answer.md"), "w", encoding="utf-8") as f:
        f.write(answer)
    with open(os.path.join(OUT, "reasoning.md"), "w", encoding="utf-8") as f:
        f.write(thought)
    with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(json.dumps(meta, indent=2), flush=True)
    if not answer.strip():
        sys.exit("empty answer")


if __name__ == "__main__":
    main()

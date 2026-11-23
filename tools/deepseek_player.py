#!/usr/bin/env python3
"""DeepSeek plays DELTARUNE through the Agent API: reads the game state, asks DeepSeek what to press, presses it.

    python3 tools/deepseek_player.py "walk to the save point and save"

The game must run with agent=<port> in dronline.ini (docs/agent-api.md). Every turn the player reads the state,
sends DeepSeek a short summary of it with the goal and the last few turns, and plays the buttons the model chose
(at most --max-frames frames a turn), until the model says the goal is reached or a limit ends the run. Version 1
of the Agent API covers the overworld and dialogue; in a battle the model sees only "battle".

The key comes from the environment variable DEEPSEEK_API_KEY, or else from a file (--key-file, default
~/.config/deltarune-online/deepseek.env) holding the line DEEPSEEK_API_KEY=<key> (a line with only the key works
too). Keep that file outside the repository, readable only by you (chmod 600). The key only goes into the
Authorization header of the requests to api.deepseek.com: it is never printed or logged.

Limits (each ends the run with exit code 3):
  --max-steps <n>     turns, default 40. A dialogue box that only waits for confirm is answered without asking the
                      model (--no-auto-confirm turns that off); such a turn counts too
  --max-usd <usd>     spending on DeepSeek, default 0.05. Counted from the tokens the API reports, at the peak
                      prices, so the real cost is the same or lower. The run stops before a call that could pass it
  --max-minutes <m>   wall time, default 20
  --max-frames <n>    input frames a turn, default 150 (5 s); a longer answer is cut there

Other options:
  --port <port>       the agent= port of dronline.ini (default 8765); --host, default 127.0.0.1
  --model <model>     flash (deepseek-flash, default) or pro (deepseek-v4-pro)
  --reasoning <r>     none (default: fast and cheap), low, high, max. Thinking costs many more output tokens, and
                      the spending limit reserves a call's whole output cap (3000, 12000, 32000 tokens), so raise
                      --max-usd with it
  --pause <s>         at least this long between two calls to DeepSeek (default 0.5)
  --log <file>        the log of every turn as JSON lines (default deepseek-player-<date>-<time>.jsonl here)
  --prompt            print the prompt of the first turn and stop, without calling DeepSeek (needs the game)
  --fake <file>       no DeepSeek and no key: the answers come from this file, one per line, either the model's JSON
                      ({"why": "...", "do": "20 right", "done": false}) or only the steps ("20 right"). After the
                      last line the fake model says the goal is reached. For tests (native/test_agent.py)

Steps are written as in tools/agent_client.py ("20 right; tap confirm 2 15"). Every turn prints one line: the
turn, what the model saw in short, why it chose what it did, what was pressed, and the money spent so far.

Exit code: 0 the model says the goal is reached, 1 the game or DeepSeek failed or the model gave up, 2 the command
line or the key is wrong, 3 a limit ended the run, 130 Ctrl+C. The queued input is dropped at every exit.

Two players online: run one player on each PC, each against its own game, with its own goal. Online, every turn
waits for the lockstep, so both games must be running.

Tested without the game and without a key (tests/test_deepseek_player.py; native/test_agent.py solo, against
drnet.dll); not yet
tested in the game or against the real API.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_client  # noqa: E402
import deepseek_review  # noqa: E402

MODELS = {"flash": "deepseek-flash", "deepseek-flash": "deepseek-flash", "deepseek-v4-flash": "deepseek-flash",
          "pro": "deepseek-v4-pro", "deepseek-v4-pro": "deepseek-v4-pro"}
# USD per 1M tokens: cache hit, cache miss, output. The peak prices of
# https://api-docs.deepseek.com/quick_start/pricing (2026-11); off-peak is half of them.
PRICES = {"deepseek-flash": (0.006, 0.3, 1.2), "deepseek-v4-pro": (0.044, 1.32, 3.96)}
# Output cap per reasoning level; thinking tokens count as output. The answer itself is about 60 tokens.
MAX_TOKENS = {"none": 300, "low": 3000, "high": 12000, "max": 32000}
DEFAULT_KEY_FILE = os.path.join("~", ".config", "deltarune-online", "deepseek.env")
HISTORY = 6             # turns the model is reminded of
MAX_NEARBY = 12         # objects of the state's nearby list in the prompt
MAX_TEXT = 300          # characters of one dialogue box in the prompt
MAX_BAD = 3             # unusable answers in a row before the run gives up

SYSTEM = """You play DELTARUNE through a program. Each turn you get the game state and the last turns, and you
answer with the buttons to press next.

Buttons: down right up left confirm cancel menu (or d r u l z x c). Steps, separated by ';':
  <frames> <buttons>            hold buttons for that many frames (30 frames = 1 s), e.g. "20 right", "10 up+left"
  wait <frames>                 press nothing
  tap <buttons> [times] [gap]   press for 1 frame, then nothing for <gap> frames, e.g. "tap confirm 2 10"
At most {max_frames} frames a turn. Then you see the new state.

The state: x grows to the right, y grows downward. Walking moves about 2 to 4 px a frame. nearby lists objects with
their offset (dx,dy) from you. can_move=false: a dialogue, cutscene or menu holds you; confirm advances dialogue,
cancel closes menus, a choice is picked with a direction and then confirm. To interact with something, stand next
to it facing it and tap confirm. If your last input changed nothing, try something else.

Goal: {goal}

Answer with one JSON object and nothing else:
{{"why": "<at most 15 words>", "do": "<steps>", "done": false}}
"done": true only when the goal is reached (then "do" may be empty). If the goal cannot be reached, answer
"done": true and say why in "why", starting with "give up:"."""


class Stop(Exception):
    """Ends the run: `code` is the exit code, `why` is said in the last line."""

    def __init__(self, code, why):
        super().__init__(why)
        self.code, self.why = code, why


# ---------------------------------------------------------------------------------------------- the prompt


def _num(v):
    return int(round(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def me_of(state):
    """The local player's entry of players_pos, or None."""
    for p in state.get("players_pos") or []:
        if isinstance(p, dict) and p.get("player") == state.get("player", 1):
            return p
    return None


FACING = {0: "down", 1: "right", 2: "up", 3: "left"}


def summarize(state):
    """The state as a few short lines for the prompt. Keys the state lacks are left out."""
    s = state or {}
    out = []
    head = []
    for k in ("chapter", "room", "can_move", "battle", "interact", "menuno", "submenu", "desync"):
        if s.get(k) is not None and not (k == "desync" and s.get(k) == -1):
            head.append("%s=%s" % (k, json.dumps(s[k]) if isinstance(s[k], bool) else s[k]))
    if head:
        out.append(" ".join(head))
    me = me_of(s)
    mx = my = None
    for p in s.get("players_pos") or []:
        if not isinstance(p, dict):
            continue
        who = "you" if p is me else "player %s" % p.get("player")
        name = (" " + str(p["name"])) if p.get("name") else ""
        facing = (" facing " + FACING.get(p["facing"], str(p["facing"]))) if p.get("facing") is not None else ""
        out.append("%s%s at x=%s y=%s%s" % (who, name, _num(p.get("x")), _num(p.get("y")), facing))
        if p is me:
            mx, my = p.get("x"), p.get("y")
    for d in (s.get("dialogue") or [])[:4]:
        if not isinstance(d, dict):
            continue
        text = " ".join(str(d.get("text") or "").split())[:MAX_TEXT]
        flag = "waits for confirm" if d.get("waiting") else "typing"
        out.append("dialogue (%s): %s" % (flag, json.dumps(text, ensure_ascii=False)))
    c = s.get("choice")
    if isinstance(c, dict):
        opts = [" ".join(str(o).split())[:60] for o in (c.get("options") or [])]
        out.append("choice: %s, selected %s (0 left, 1 right, 2 up, 3 down)" % (
            json.dumps(opts, ensure_ascii=False), c.get("selected")))
    sm = s.get("save_menu")
    if isinstance(sm, dict):
        # The key of the Agent API's save point menu (PR 37 of the game side); the numbers are the game's own.
        out.append("save point menu: %s. stage 0: main box, cursor x,y = Save (0,0), Return (1,0), Storage (0,1), "
                   "Recruits (1,1), x=2 a saved message waits for confirm; stage 1: file slot 0-2, 3 Return; "
                   "overwrite=1: overwrite question, overwrite_x 0 Save 1 Return"
                   % json.dumps(sm, separators=(",", ":"))[:200])
    near = []
    for o in (s.get("nearby") or [])[:MAX_NEARBY]:
        if not isinstance(o, dict):
            continue
        x, y = o.get("x"), o.get("y")
        if isinstance(mx, (int, float)) and isinstance(x, (int, float)) and isinstance(y, (int, float)):
            where = "(%+d,%+d)" % (round(x - mx), round(y - my))
        else:
            where = "(%s,%s)" % (_num(x), _num(y))
        near.append("%s %s%s" % (o.get("object"), where, " solid" if o.get("solid") else ""))
    if near:
        out.append("nearby: " + "; ".join(near))
    return "\n".join(out) if out else json.dumps(s)[:1000]


def change(before, after):
    """What a turn changed, in a few words."""
    if not before or not after:
        return "?"
    bits = []
    if before.get("room") != after.get("room"):
        bits.append("room %s -> %s" % (before.get("room"), after.get("room")))
    a, b = me_of(before), me_of(after)
    if a and b and isinstance(a.get("x"), (int, float)) and isinstance(b.get("x"), (int, float)):
        dx, dy = round(b["x"] - a["x"]), round(b["y"] - a["y"])
        if dx or dy:
            bits.append("moved (%+d,%+d)" % (dx, dy))
    for k in ("can_move", "battle", "interact", "menuno"):
        if before.get(k) != after.get(k):
            bits.append("%s %s -> %s" % (k, before.get(k), after.get(k)))
    texts = lambda s: [d.get("text") for d in (s.get("dialogue") or []) if isinstance(d, dict)]  # noqa: E731
    if texts(before) != texts(after):
        bits.append("dialogue changed")
    if bool(before.get("choice")) != bool(after.get("choice")):
        bits.append("choice " + ("appeared" if after.get("choice") else "gone"))
    return ", ".join(bits) or "nothing changed"


def messages(goal, max_frames, state, history, turn, max_steps):
    """The system prompt stays the same for the whole run, so DeepSeek's context cache bills it at the cache-hit
    price from the second call on; only the short user message changes."""
    recent = "\n".join(history[-HISTORY:]) or "(first turn)"
    user = "Turn %d of %d.\nLast turns:\n%s\nState now:\n%s" % (turn, max_steps, recent, summarize(state))
    return [{"role": "system", "content": SYSTEM.format(goal=goal, max_frames=max_frames)},
            {"role": "user", "content": user}]


# ---------------------------------------------------------------------------------------------- the answer


def parse_answer(content, max_frames):
    """The model's text -> (why, steps text as given, per-frame bits cut to max_frames, done).
    Raises ValueError with what is wrong."""
    text = (content or "").strip()
    try:
        obj = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)        # a JSON object inside a code fence or other words
        if not m:
            raise ValueError("not JSON: %r" % text[:200])
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            raise ValueError("not JSON: %r" % text[:200])
    if not isinstance(obj, dict):
        raise ValueError("not a JSON object: %r" % text[:200])
    why = " ".join(str(obj.get("why") or "").split())[:200]
    do = obj.get("do") or ""
    if not isinstance(do, str):
        raise ValueError('"do" must be a string of steps, not %r' % (do,))
    done = obj.get("done") is True
    frames = agent_client.text_to_frames(do)        # ScriptError is a ValueError
    agent_client.frames_to_steps(frames)            # only the seven buttons of the agent
    return why, do.strip(), frames[:max_frames], done


# ---------------------------------------------------------------------------------------------- models


def usage_cost(usage, prices):
    """USD for one call, from the token counts the API returned."""
    u = usage or {}
    hit = u.get("prompt_cache_hit_tokens", 0) or 0
    miss = u.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = max((u.get("prompt_tokens", 0) or 0) - hit, 0)
    out = u.get("completion_tokens", 0) or 0
    return (hit * prices[0] + miss * prices[1] + out * prices[2]) / 1e6


class DeepSeek:
    """DeepSeek's chat completions API (the request and the retries of tools/deepseek_review.py)."""

    def __init__(self, key, model, reasoning, http=deepseek_review.http_json, sleep=time.sleep):
        self.key, self.model, self.reasoning, self.http, self.sleep = key, model, reasoning, http, sleep
        self.max_tokens = MAX_TOKENS[reasoning]

    def ask(self, msgs):
        """(the answer's text, usage). Raises RuntimeError with a message without the key."""
        body = deepseek_review.request_body(self.model, self.reasoning, msgs, MAX_TOKENS)
        body["response_format"] = {"type": "json_object"}
        try:
            answer = deepseek_review.call_deepseek(self.key, body, self.http, self.sleep, attempts=2, timeout=60,
                                                   budget=120)
        except RuntimeError as e:
            raise RuntimeError(deepseek_review.redact(str(e), self.key))
        choice = (answer.get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or "", answer.get("usage") or {}


class FakeModel:
    """Answers from a list, for tests: no network, no key. Its token counts are made up from the prompt's length,
    so that the spending limit can be tested too."""

    max_tokens = 100

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    @classmethod
    def from_file(cls, path):
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line if line.startswith("{") else json.dumps({"why": "fake", "do": line}))
        return cls(out)

    def ask(self, msgs):
        self.asked.append(msgs)
        text = self.answers.pop(0) if self.answers else '{"why": "fake answers ran out", "do": "", "done": true}'
        chars = sum(len(m["content"]) for m in msgs)
        return text, {"prompt_tokens": chars // 4, "prompt_cache_hit_tokens": 0, "completion_tokens": len(text) // 4}


def key_from_text(text):
    """The key in the text of a key file: the value of a DEEPSEEK_API_KEY=... line (with or without 'export' and
    quotes), or else the first line that is not empty or a # comment."""
    bare = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if m:
            if m.group(1) == "DEEPSEEK_API_KEY":
                return m.group(2).strip().strip("'\"").strip()
        elif not bare:
            bare = line
    return bare


def load_key(env, key_file):
    """The API key: DEEPSEEK_API_KEY, or else the key file. Raises Stop(2) without one."""
    key = (env.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key
    path = os.path.expanduser(key_file)
    try:
        with open(path, encoding="utf-8") as f:
            key = key_from_text(f.read(4096))
    except OSError:
        key = ""
    if not key:
        raise Stop(2, "no DeepSeek key: set DEEPSEEK_API_KEY, or put the line DEEPSEEK_API_KEY=<key> in %s" % path)
    return key


# ---------------------------------------------------------------------------------------------- the run


class Log:
    """One JSON line per turn in the log file, and one short line per turn on the screen."""

    def __init__(self, path, out=sys.stdout):
        self.path, self.out = path, out
        self.f = open(path, "a", encoding="utf-8") if path else None

    def write(self, record, line=None):
        if self.f:
            self.f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.f.flush()
        if line is not None:
            print(line, file=self.out)
            self.out.flush()

    def close(self):
        if self.f:
            self.f.close()


def short(state):
    """The state in a few words for the screen."""
    me = me_of(state or {}) or {}
    bits = [str(state.get("room") or "?")]
    if isinstance(me.get("x"), (int, float)):
        bits.append("(%d,%d)" % (round(me["x"]), round(me["y"])))
    if state.get("can_move") is False:
        bits.append("busy")
    if state.get("battle"):
        bits.append("battle")
    if any(isinstance(d, dict) for d in state.get("dialogue") or []):
        bits.append("dialogue")
    if state.get("choice"):
        bits.append("choice")
    return " ".join(bits)


def only_waiting_dialogue(state):
    """A dialogue box waits for confirm, nothing else is to be decided: no choice, no battle."""
    boxes = [d for d in state.get("dialogue") or [] if isinstance(d, dict)]
    return bool(boxes) and any(d.get("waiting") for d in boxes) and not state.get("choice") and not state.get("battle")


def play(game, model, goal, *, max_steps=40, max_usd=0.05, max_minutes=20.0, max_frames=150, pause=0.5,
         prices=PRICES["deepseek-flash"], auto_confirm=True, log=None, tally=None, clock=time.monotonic,
         sleep=time.sleep):
    """The loop. Returns (0, why it ended) when the model says the goal is reached, raises Stop otherwise.
    tally["usd"] holds the USD spent so far, also after a Stop."""
    log = log or Log(None)
    tally = {} if tally is None else tally
    tally["usd"] = spent = 0.0
    stuck_dialogue = False
    start = clock()
    last_call = None
    history = []
    bad = 0
    state = game.state()
    log.write({"event": "start", "goal": goal, "state": summarize(state)},
              "goal: %s\nstart: %s" % (goal, short(state)))
    for turn in range(1, max_steps + 1):
        if clock() - start > max_minutes * 60:
            raise Stop(3, "time limit (%g min)" % max_minutes)
        # A box that waits for confirm is answered without the model, unless confirm changed nothing last time.
        if auto_confirm and only_waiting_dialogue(state) and not stuck_dialogue:
            before = state
            state = game.do("tap confirm; wait 4")
            what = change(before, state)
            stuck_dialogue = what == "nothing changed"
            said = " / ".join(" ".join(str(d.get("text") or "").split())
                              for d in before.get("dialogue") or [] if isinstance(d, dict))
            history.append("turn %d: dialogue %s, tap confirm -> %s" % (
                turn, json.dumps(said[:160], ensure_ascii=False), what))
            log.write({"event": "turn", "turn": turn, "auto": True, "dialogue": said, "do": "tap confirm; wait 4",
                       "result": what},
                      "%3d  %-34s  %s | tap confirm -> %s" % (
                          turn, short(before)[:34], json.dumps(said[:60], ensure_ascii=False), what))
            continue
        stuck_dialogue = False
        msgs = messages(goal, max_frames, state, history, turn, max_steps)
        # The next call costs at most: the whole prompt at the cache-miss price (about 3 characters a token) and
        # the whole output cap.
        worst = (sum(len(m["content"]) for m in msgs) / 3 * prices[1] + model.max_tokens * prices[2]) / 1e6
        if spent + worst > max_usd:
            raise Stop(3, "spending limit: %.4f of %.4f USD spent, the next call could cost %.4f" % (
                spent, max_usd, worst))
        if last_call is not None and clock() - last_call < pause:
            sleep(pause - (clock() - last_call))
        last_call = clock()
        content, usage = model.ask(msgs)
        cost = usage_cost(usage, prices)
        spent += cost
        tally["usd"] = spent
        record = {"event": "turn", "turn": turn, "state": msgs[1]["content"].split("State now:\n", 1)[1],
                  "answer": content, "usage": usage, "usd": round(cost, 6), "usd_total": round(spent, 6)}
        try:
            why, do, frames, done = parse_answer(content, max_frames)
        except ValueError as e:
            bad += 1
            history.append("turn %d: your answer was unusable (%s); answer only the JSON object" % (turn, e))
            record["error"] = str(e)
            log.write(record, "%3d  %-34s  unusable answer: %s  [%.4f USD]" % (turn, short(state)[:34], e, spent))
            if bad >= MAX_BAD:
                raise Stop(1, "%d unusable answers in a row" % bad)
            continue
        bad = 0
        record.update(why=why, do=do, done=done, frames=len(frames))
        if done:
            log.write(record, "%3d  %-34s  %s  [%.4f USD]" % (turn, short(state)[:34], why or "done", spent))
            if why.lower().startswith("give up"):
                raise Stop(1, "the model gave up: %s" % why)
            return 0, "the model says the goal is reached: %s" % why
        before = state
        if frames:
            state = game.send_steps(agent_client.frames_to_steps(frames))
        else:
            state = game.state()
        what = change(before, state)
        cut = " (cut to %d frames)" % max_frames if len(agent_client.text_to_frames(do)) > max_frames else ""
        history.append("turn %d: %s -> %s%s" % (turn, do or "(nothing)", what, cut))
        record["result"] = what
        log.write(record, "%3d  %-34s  %s | %s%s -> %s  [%.4f USD]" % (
            turn, short(before)[:34], why, do or "-", cut, what, spent))
    raise Stop(3, "step limit (%d)" % max_steps)


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("goal", help='in plain words, e.g. "walk to the save point and save"')
    ap.add_argument("--port", type=int, default=agent_client.DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for the game's answer")
    ap.add_argument("--model", default="flash")
    ap.add_argument("--reasoning", default="none", choices=sorted(MAX_TOKENS))
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--max-usd", type=float, default=0.05)
    ap.add_argument("--max-minutes", type=float, default=20.0)
    ap.add_argument("--max-frames", type=int, default=150)
    ap.add_argument("--pause", type=float, default=0.5)
    ap.add_argument("--no-auto-confirm", action="store_true")
    ap.add_argument("--key-file", default=DEFAULT_KEY_FILE)
    ap.add_argument("--log", default=None)
    ap.add_argument("--prompt", action="store_true")
    ap.add_argument("--fake", metavar="FILE")
    args = ap.parse_args(argv)
    if args.model.lower() not in MODELS:
        ap.error("unknown model %s: flash or pro" % args.model)
    args.model = MODELS[args.model.lower()]
    if args.max_steps < 1 or args.max_usd <= 0 or args.max_minutes <= 0 or not 1 <= args.max_frames <= 3600:
        ap.error("--max-steps, --max-usd and --max-minutes must be above 0, --max-frames 1 to 3600")
    if not args.goal.strip():
        ap.error("the goal is empty")
    return args


def main(argv, env=os.environ, out=sys.stdout, http=deepseek_review.http_json):
    args = parse_args(argv)
    goal = " ".join(args.goal.split())
    try:
        if args.fake:
            model = FakeModel.from_file(args.fake)
        elif args.prompt:
            model = None
        else:
            model = DeepSeek(load_key(env, args.key_file), args.model, args.reasoning, http)
    except Stop as e:
        print("error: %s" % e.why, file=sys.stderr)
        return e.code
    except OSError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    path = args.log or ("" if args.prompt else
                        "deepseek-player-%s.jsonl" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    game = agent_client.AgentClient(args.host, args.port, args.timeout)
    log = Log(path, out)
    code, why, tally = 0, "", {"usd": 0.0}
    try:
        game.connect()
        if args.prompt:
            for m in messages(goal, args.max_frames, game.state(), [], 1, args.max_steps):
                print("--- %s\n%s" % (m["role"], m["content"]), file=out)
            return 0
        print("model %s, reasoning %s; limits: %d steps, %.4f USD, %g min; log %s" % (
            "fake" if args.fake else args.model, args.reasoning, args.max_steps, args.max_usd, args.max_minutes,
            path), file=out)
        code, why = play(game, model, goal, max_steps=args.max_steps, max_usd=args.max_usd,
                                max_minutes=args.max_minutes, max_frames=args.max_frames, pause=args.pause,
                                prices=PRICES.get(args.model, PRICES["deepseek-v4-pro"]),
                                auto_confirm=not args.no_auto_confirm, log=log, tally=tally)
    except Stop as e:
        code, why = e.code, e.why
    except agent_client.AgentError as e:
        code, why = 1, "game: %s" % e
    except RuntimeError as e:
        code, why = 1, "DeepSeek: %s" % e
    except KeyboardInterrupt:
        code, why = 130, "stopped with Ctrl+C"
    finally:
        if game.sock is not None:
            try:
                game.clear()
            except (agent_client.AgentError, OSError):
                pass
            game.close()
        if not args.prompt:
            log.write({"event": "end", "code": code, "why": why, "usd_total": round(tally["usd"], 6)},
                      "end: %s; %.4f USD at peak prices (exit %d)" % (why, tally["usd"], code))
        log.close()
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

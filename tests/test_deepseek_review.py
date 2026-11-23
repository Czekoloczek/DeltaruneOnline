"""tools/deepseek_review.py: options from the /deepseek-review comment, diff trimming, and the whole run against
fake GitHub and DeepSeek APIs (no network, no key)."""
import json
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import deepseek_review as r  # noqa: E402

KEY = "sk-test0123456789abcdef"


class Options(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(r.parse_request("/deepseek-review"),
                         {"model": "deepseek-flash", "reasoning": "high", "note": ""})

    def test_not_a_command(self):
        for body in ("", "looks good", "/deepseek-reviewer", "please /deepseek-review"):
            self.assertIsNone(r.parse_request(body), body)

    def test_model_reasoning_and_note(self):
        o = r.parse_request("/DeepSeek-Review model=pro reasoning=MAX check the heart hooks\nand the Draw event")
        self.assertEqual(o["model"], "deepseek-v4-pro")
        self.assertEqual(o["reasoning"], "max")
        self.assertEqual(o["note"], "check the heart hooks\nand the Draw event")

    def test_aliases(self):
        self.assertEqual(r.parse_request("/deepseek-review model=deepseek-v4-flash effort=off")["model"],
                         "deepseek-flash")
        self.assertEqual(r.parse_request("/deepseek-review thinking=off")["reasoning"], "none")

    def test_bad_values_are_refused(self):
        for body in ("/deepseek-review model=deepseek-chat", "/deepseek-review reasoning=medium",
                     "/deepseek-review reasonig=max"):
            with self.assertRaises(r.RequestError, msg=body):
                r.parse_request(body)

    def test_url_in_note_is_not_an_option(self):
        self.assertEqual(r.parse_request("/deepseek-review see https://x.test/?a=b")["note"], "see https://x.test/?a=b")

    def test_manual_run_inputs(self):
        self.assertEqual(r.parse_options("deepseek-v4-pro", "none", " n ")["note"], "n")
        with self.assertRaises(r.RequestError):
            r.parse_options("gpt", None)

    def test_request_body(self):
        b = r.request_body("deepseek-flash", "none", [])
        self.assertEqual(b["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", b)
        b = r.request_body("deepseek-flash", "low", [])
        self.assertEqual((b["thinking"], b["reasoning_effort"]), ({"type": "enabled"}, "low"))
        self.assertNotIn("temperature", b)
        self.assertEqual(r.request_body("deepseek-v4-pro", "max", [])["max_tokens"], 64000)


def f(name, patch="@@ -1 +1 @@\n-a\n+b", **kw):
    return dict({"filename": name, "status": "modified", "additions": 1, "deletions": 1, "patch": patch}, **kw)


class Diff(unittest.TestCase):
    def test_core_first(self):
        diff, notes, sent, full = r.build_diff([f("README.md"), f("tools/x.py"), f("gml/scr_dronline.gml")])
        order = [line.split(" b/")[1] for line in diff.splitlines() if line.startswith("diff --git")]
        self.assertEqual(order, ["gml/scr_dronline.gml", "tools/x.py", "README.md"])
        self.assertEqual((notes, sent), ({}, full))

    def test_big_file_is_cut(self):
        big = "@@ -1,9000 +1,9000 @@\n" + "+x = 1;\n" * 9000
        diff, notes, sent, full = r.build_diff([f("native/drnet.c", big)], max_total=60_000, max_file=5_000)
        self.assertEqual(notes, {"native/drnet.c": "ucięty"})
        self.assertLess(sent, 5_200)
        self.assertGreater(full, 70_000)
        self.assertIn("ucięto", diff)

    def test_budget_leaves_files_out(self):
        files = [f(f"docs/{i}.md", "+" + "y" * 3_000) for i in range(10)]
        diff, notes, sent, full = r.build_diff(files, max_total=10_000, max_file=20_000)
        self.assertLessEqual(sent, 10_200)
        self.assertTrue(any(v.startswith("pominięty") for v in notes.values()))
        self.assertEqual(len(notes), 7)  # three whole files fit, the fourth would be a 1 000-char stub

    def test_binary_and_rename(self):
        block = r.file_block(f("a.png", None, previous_filename="b.png", status="renamed"))
        self.assertIn("diff --git a/b.png b/a.png", block)
        self.assertIn("binarny", block)

    def test_design_excerpt_finds_the_sections(self):
        with open(os.path.join(ROOT, "docs", "DESIGN.md"), encoding="utf-8") as fh:
            text = fh.read()
        ex = r.design_excerpt(text)
        for heading in ("## Lockstep", "## Determinism hazards", "## Pitfalls"):
            self.assertIn(heading, ex)
        self.assertLessEqual(len(ex), r.MAX_DESIGN_CHARS)
        self.assertNotIn("## Playing over WireGuard", ex)

    def test_design_excerpt_fallback(self):
        self.assertEqual(r.design_excerpt("# Other\ntext"), "# Other\ntext")


class Output(unittest.TestCase):
    def test_mentions_do_not_notify(self):
        self.assertEqual(r.neutralize_mentions("ask @someone, mail a@b"), "ask @⁠someone, mail a@⁠b")

    def test_redact(self):
        msg = f"Authentication Fails, Your api key: ****cdef is invalid ({KEY})"
        out = r.redact(msg, KEY)
        self.assertNotIn(KEY, out)
        self.assertNotIn("cdef", out)


class FakeAPI:
    """GitHub and DeepSeek in one; records every call."""

    def __init__(self, fork=False, deepseek=None, files=None):
        self.calls, self.comments, self.deepseek_bodies = [], [], []
        self.fork = fork
        self.deepseek = deepseek or [(200, {"choices": [{"message": {"content": "- **ważne** `gml/a.gml:3`: x"},
                                                          "finish_reason": "stop"}],
                                             "usage": {"prompt_tokens": 1200, "completion_tokens": 300,
                                                       "prompt_cache_hit_tokens": 1000,
                                                       "completion_tokens_details": {"reasoning_tokens": 200}}})]
        self.files = files if files is not None else [f("gml/a.gml"), f("README.md")]

    def __call__(self, method, url, headers, body=None, timeout=60):
        self.calls.append((method, url, headers))
        if url == r.API_URL:
            self.deepseek_bodies.append(body)
            return self.deepseek.pop(0)
        if url.endswith("/pulls/7"):
            head_repo = {"full_name": "someone/fork" if self.fork else "o/r"}
            return 200, {"number": 7, "title": "T", "body": "B", "additions": 2, "deletions": 2,
                         "user": {"login": "dev"}, "head": {"ref": "feat", "sha": "abcdef1234", "repo": head_repo},
                         "base": {"ref": "main", "repo": {"full_name": "o/r"}}}
        if "/pulls/7/files" in url:
            return 200, self.files
        if url.endswith("/issues/7/comments"):
            self.comments.append(body["body"])
            return 201, {}
        if "/reactions" in url:
            return 201, {}
        return 404, {"message": "Not Found"}


def env(**kw):
    base = {"GITHUB_REPOSITORY": "o/r", "GH_TOKEN": "ghs_x", "PR": "7", "DEEPSEEK_API_KEY": KEY,
            "REQUEST": "/deepseek-review reasoning=low", "COMMENT_ID": "99", "REQUESTER": "kuba"}
    base.update(kw)
    return base


class Run(unittest.TestCase):
    def run_review(self, api, **kw):
        return r.run(env(**kw), http=api, sleep=lambda s: None)

    def test_review_is_posted_once(self):
        api = FakeAPI()
        self.assertEqual(self.run_review(api), 0)
        self.assertEqual(len(api.comments), 1)
        c = api.comments[0]
        self.assertTrue(c.startswith(r.MARKER))
        self.assertIn("gml/a.gml:3", c)
        self.assertIn("`deepseek-flash`, reasoning `low`", c)
        self.assertIn("200 na myślenie", c)
        body = api.deepseek_bodies[0]
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertIn("ROZJAZD", body["messages"][0]["content"])
        self.assertIn("## Lockstep", body["messages"][0]["content"])
        self.assertIn("diff --git a/gml/a.gml", body["messages"][1]["content"])
        self.assertTrue(any("/reactions" in url for _, url, _ in api.calls))

    def test_key_only_goes_to_deepseek(self):
        api = FakeAPI()
        self.run_review(api)
        for _, url, headers in api.calls:
            has_key = any(KEY in v for v in headers.values())
            self.assertEqual(has_key, url == r.API_URL, url)
        self.assertNotIn(KEY, api.comments[0])

    def test_missing_key_is_skipped_with_a_comment(self):
        api = FakeAPI()
        self.assertEqual(self.run_review(api, DEEPSEEK_API_KEY=""), 0)
        self.assertEqual(len(api.comments), 1)
        self.assertIn("DEEPSEEK_API_KEY", api.comments[0])
        self.assertFalse(api.deepseek_bodies)

    def test_fork_is_skipped(self):
        api = FakeAPI(fork=True)
        self.assertEqual(self.run_review(api), 0)
        self.assertIn("forka", api.comments[0])
        self.assertFalse(api.deepseek_bodies)
        self.assertFalse(any("/files" in url for _, url, _ in api.calls))

    def test_bad_option_comments_usage(self):
        api = FakeAPI()
        self.assertEqual(self.run_review(api, REQUEST="/deepseek-review model=gpt"), 0)
        self.assertIn("Użycie", api.comments[0])
        self.assertFalse(api.deepseek_bodies)

    def test_other_comment_does_nothing(self):
        api = FakeAPI()
        self.assertEqual(self.run_review(api, REQUEST="thanks"), 0)
        self.assertEqual(api.calls, [])

    def test_manual_run(self):
        api = FakeAPI()
        code = self.run_review(api, REQUEST="", COMMENT_ID="", INPUT_MODEL="deepseek-v4-pro",
                               INPUT_REASONING="none", INPUT_NOTE="focus on drnet")
        self.assertEqual(code, 0)
        body = api.deepseek_bodies[0]
        self.assertEqual((body["model"], body["thinking"]["type"]), ("deepseek-v4-pro", "disabled"))
        self.assertIn("focus on drnet", body["messages"][1]["content"])
        self.assertFalse(any("/reactions" in url for _, url, _ in api.calls))

    def test_retry_then_success(self):
        ok = FakeAPI().deepseek[0]
        api = FakeAPI(deepseek=[(503, {"error": {"message": "busy"}}), ok])
        self.assertEqual(self.run_review(api), 0)
        self.assertEqual(len(api.deepseek_bodies), 2)
        self.assertIn("Przegląd DeepSeek", api.comments[0])

    def test_auth_error_is_reported_without_the_key(self):
        api = FakeAPI(deepseek=[(401, {"error": {"message": f"Your api key: ****{KEY[-4:]} is invalid"}})])
        self.assertEqual(self.run_review(api), 1)
        self.assertEqual(len(api.deepseek_bodies), 1)  # 401 is not retried
        self.assertIn("klucz API", api.comments[0])
        self.assertNotIn(KEY[-4:], api.comments[0])

    def test_empty_answer_after_thinking(self):
        api = FakeAPI(deepseek=[(200, {"choices": [{"message": {"content": "  "}, "finish_reason": "length"}]})])
        self.assertEqual(self.run_review(api), 1)
        self.assertIn("myślenie", api.comments[0])

    def test_bad_pr_number(self):
        self.assertEqual(self.run_review(FakeAPI(), PR="7; rm -rf /"), 1)

    def test_workflow_matches_the_script(self):
        """Manual-run choices in the workflow are values the script accepts, and its defaults match."""
        import re
        with open(os.path.join(ROOT, ".github", "workflows", "deepseek-review.yml"), encoding="utf-8") as fh:
            wf = fh.read()
        models = re.search(r"default: (\S+)\n\s+options: \[(deepseek-[^\]]+)\]", wf)
        self.assertEqual(r.MODELS[models.group(1)], r.DEFAULT_MODEL)
        for m in models.group(2).split(", "):
            self.assertIn(m, r.MODELS)
        levels = re.search(r"default: (\S+)\n\s+options: \[(none[^\]]+)\]", wf)
        self.assertEqual(levels.group(1), r.DEFAULT_REASONING)
        self.assertEqual(levels.group(2).split(", "), ["none", "low", "high", "max"])
        self.assertIn("python3 -I tools/deepseek_review.py", wf)
        self.assertIn(r.COMMAND, wf)

    def test_only_real_requests_share_a_concurrency_group(self):
        """A /deepseek-review comment from someone off the list must not join (and cancel) the PR's group,
        so the group uses the job's own condition, allowlist included."""
        import re
        with open(os.path.join(ROOT, ".github", "workflows", "deepseek-review.yml"), encoding="utf-8") as fh:
            wf = fh.read()
        group = re.search(r"^  group: (.+)$", wf, re.M).group(1).replace('\\"', '"')
        job_if = re.search(r"^    if: >-\n((?:      .+\n)+)", wf, re.M).group(1)
        job_if = " ".join(job_if.split())
        self.assertIn(job_if, " ".join(group.split()))
        self.assertNotIn("author_association", job_if)

    def test_only_listed_people_can_request(self):
        """The job runs only for the two listed logins, checked for the comment's author (or whoever started the
        manual run) and for whoever started this very run (a Re-run), and only for newly created comments."""
        import re
        with open(os.path.join(ROOT, ".github", "workflows", "deepseek-review.yml"), encoding="utf-8") as fh:
            wf = fh.read()
        job_if = " ".join(re.search(r"^    if: >-\n((?:      .+\n)+)", wf, re.M).group(1).split())
        allow = """contains(fromJSON('["PolskiKapsel", "Czekoloczek"]'), """
        self.assertIn(allow + "github.event.comment.user.login || github.actor)", job_if)
        self.assertIn(allow + "github.triggering_actor)", job_if)
        self.assertEqual(job_if.count("fromJSON("), 2)  # no second, wider list (e.g. author_association)
        self.assertRegex(wf, r"issue_comment:\n\s+types: \[created\]\n")


if __name__ == "__main__":
    unittest.main()

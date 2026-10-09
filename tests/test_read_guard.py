"""Tests for the read-guard plugin. Run: python3 -m unittest discover -s tests"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "read-guard", "scripts"))

from readguard import core  # noqa: E402

BODY = "".join("line %d: some reasonably long content to make the file worth guarding\n" % i for i in range(1, 121))


class RG(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rg-")
        os.environ["CLAUDE_PLUGIN_DATA"] = os.path.join(self.tmp, "data")
        self.f = os.path.join(self.tmp, "a.py")
        self.write(BODY)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text):
        with open(self.f, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self, offset=None, limit=None, sid="s1", agent=None, native=False):
        with open(self.f, encoding="utf-8") as fh:
            text = fh.read()
        lines = text.splitlines(True)
        start = offset or 1
        sel = lines[start - 1:(start - 1 + limit) if limit else None]
        if native:
            resp = {"type": "file_unchanged", "file": {"filePath": self.f},
                    "message": "Wasted call — file unchanged since your last Read."}
        else:
            resp = {"type": "text", "file": {"filePath": self.f, "content": "".join(sel), "numLines": len(sel),
                                             "startLine": start, "totalLines": len(lines)}}
        ti = {"file_path": self.f}
        if offset:
            ti["offset"] = offset
        if limit:
            ti["limit"] = limit
        inp = {"session_id": sid, "cwd": self.tmp, "tool_name": "Read", "tool_input": ti, "tool_response": resp}
        if agent:
            inp["agent_id"] = agent
        return self.hook("read", inp)

    def hook(self, event, inp):
        buf = io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(inp))
        try:
            with redirect_stdout(buf):
                core.hook_main(event)
        finally:
            sys.stdin = old
        out = buf.getvalue()
        return json.loads(out) if out.strip() else None

    def content(self, out):
        return out["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]

    def stats(self):
        return core.load_json(os.path.join(core.data_dir(), "stats.json"), {})

    def test_first_read_passes_second_is_stub_then_escape(self):
        self.assertIsNone(self.read())
        out = self.read()
        self.assertIn("Unchanged since your earlier Read", self.content(out))
        self.assertEqual(out["hookSpecificOutput"]["updatedToolOutput"]["type"], "text")
        self.assertIsNone(self.read())  # exact repeat -> full text (escape hatch)
        self.assertIsNotNone(self.read())  # and guarding resumes afterwards
        st = self.stats()
        self.assertEqual(st["stubbed"], 2)
        self.assertEqual(st["escape_full"], 1)
        self.assertGreater(st["chars_saved"], 5000)

    def test_subrange_of_seen_is_stubbed_new_range_passes(self):
        self.read(offset=1, limit=60)
        self.assertIsNotNone(self.read(offset=10, limit=20))
        self.assertIsNone(self.read(offset=61, limit=40))

    def test_change_gives_diff_only_after_whole_read(self):
        self.read()
        self.write(BODY.replace("line 50:", "line 50 CHANGED:"))
        out = self.read()
        c = self.content(out)
        self.assertIn("changed since your earlier full Read", c)
        self.assertIn("+line 50 CHANGED:", c)
        self.assertLess(len(c), 2000)
        # after the diff, an unchanged re-read is a stub again
        self.read()  # escape repeat of the diffed read -> full
        self.assertIn("Unchanged", self.content(self.read()))

    def test_change_after_partial_read_passes_full(self):
        self.read(offset=1, limit=30)
        self.write(BODY.replace("line 5:", "line 5 X:"))
        self.assertIsNone(self.read())

    def test_big_change_passes_full(self):
        self.read()
        self.write(BODY.upper())
        self.assertIsNone(self.read())

    def test_native_stub_with_outside_change_is_fixed(self):
        self.read()
        self.write(BODY + "appended outside Claude\n")
        out = self.read(native=True)
        c = self.content(out)
        self.assertIn("changed on disk", c)
        self.assertIn("appended outside Claude", c)
        self.assertEqual(self.stats()["stale_native_fixed"], 1)

    def test_native_stub_unchanged_left_alone(self):
        self.read()
        self.assertIsNone(self.read(native=True))
        self.assertEqual(self.stats()["native_stub"], 1)

    def test_separate_contexts_for_subagents_and_sessions(self):
        self.read()
        self.assertIsNone(self.read(agent="sub1"))
        self.assertIsNone(self.read(sid="s2"))

    def test_compaction_forgets(self):
        self.read()
        self.hook("pre-compact", {"session_id": "s1"})
        self.assertIsNone(self.read())
        self.assertIsNotNone(self.read())
        self.hook("session-start", {"session_id": "s1", "source": "compact"})
        self.assertIsNone(self.read())

    def test_small_files_and_images_ignored(self):
        self.write("tiny\n")
        self.read()
        self.assertIsNone(self.read())
        img = os.path.join(self.tmp, "x.png")
        with open(img, "wb") as fh:
            fh.write(b"\x89PNG....")
        self.assertIsNone(self.hook("read", {"session_id": "s1", "tool_input": {"file_path": img}, "tool_response": {}}))

    def test_off_switch(self):
        with redirect_stdout(io.StringIO()):
            core.cli(["off"])
        self.read()
        self.assertIsNone(self.read())
        with redirect_stdout(io.StringIO()):
            core.cli(["on"])

    def test_bash_observe_only(self):
        inp = {"session_id": "s1", "tool_input": {"command": "sed -n '1,50p' a.py"},
               "tool_response": {"stdout": BODY[:3000], "stderr": ""}}
        self.assertIsNone(self.hook("bash", inp))
        self.assertIsNone(self.hook("bash", inp))
        st = self.stats()
        self.assertEqual(st["bash_readlike"], 2)
        self.assertEqual(st["bash_repeat"], 1)
        self.assertIsNone(self.hook("bash", {"session_id": "s1", "tool_input": {"command": "npm test"}, "tool_response": {"stdout": "x"}}))

    def test_garbage_input(self):
        old = sys.stdin
        sys.stdin = io.StringIO("nope")
        try:
            self.assertEqual(core.hook_main("read"), 0)
        finally:
            sys.stdin = old

    def test_stats_cli(self):
        self.read(); self.read()
        buf = io.StringIO()
        with redirect_stdout(buf):
            core.cli(["stats"])
        self.assertIn("replaced by stub       : 1", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

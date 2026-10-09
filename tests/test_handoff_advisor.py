"""Tests for handoff (M2) and advisor (M3). Run: python3 -m unittest discover -s tests"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "ctx-kit", "scripts"))

from ctxkit import advisor, config, handoff, hooks, transcript, writer  # noqa: E402


def line(**d):
    return json.dumps(d)


def asst(text=None, tool=None, usage=None, tool_id="t1", model="claude-sonnet-5"):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool_use", "id": tool_id, "name": tool[0], "input": tool[1]})
    msg = {"role": "assistant", "content": content, "model": model}
    if usage:
        msg["usage"] = usage
    return line(type="assistant", message=msg)


def user(text=None, result=None, tool_id="t1", is_error=False):
    if result is not None:
        c = [{"type": "tool_result", "tool_use_id": tool_id, "content": result, "is_error": is_error}]
    else:
        c = text
    return line(type="user", message={"role": "user", "content": c})


def usage(n):
    return {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": n - 10, "output_tokens": 5}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ctxkit-ho-")
        self.data = os.path.join(self.tmp, ".data")
        os.environ["CLAUDE_PLUGIN_DATA"] = self.data
        self.home = tempfile.mkdtemp(prefix="ctxkit-home-")
        os.environ["CLAUDE_CONFIG_DIR"] = self.home
        self.sid = "sess-1234abcd"
        self.tp = os.path.join(self.home, "projects", "p", "%s.jsonl" % self.sid)
        os.makedirs(os.path.dirname(self.tp))
        self.ws = config.Workspace(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def transcript(self, lines):
        with open(self.tp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def hook(self, event, payload):
        buf = io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        try:
            with redirect_stdout(buf):
                hooks.main(event, self.data)
        finally:
            sys.stdin = old
        out = buf.getvalue()
        return json.loads(out) if out.strip() else {}


class TestTranscript(Base):
    def test_digest_keeps_answers_plans_agents_and_clips(self):
        big = "x" * 5000
        self.transcript([
            user("Please build the parser"),
            asst("Choosing regex over a grammar because inputs are tiny.", ("AskUserQuestion", {"questions": [{"question": "Which DB?"}]})),
            user(result="Postgres, not MySQL", tool_id="t1"),
            asst(None, ("ExitPlanMode", {"plan": "PLAN BODY"}), tool_id="t2"),
            user(result="On selected text: do X instead", tool_id="t2", is_error=True),
            asst(None, ("Bash", {"command": "ls -la"}), tool_id="t3"),
            user(result=big, tool_id="t3"),
            asst(None, ("Agent", {"description": "research"}), tool_id="t4"),
            user(result=[{"type": "text", "text": "AGENT FINDINGS"}], tool_id="t4"),
            line(type="assistant", isSidechain=True, message={"role": "assistant", "content": [{"type": "text", "text": "SIDECHAIN"}]}),
        ])
        text = "\n".join(transcript.digest(self.tp))
        for s in ("Please build the parser", "regex over a grammar", "Postgres, not MySQL", "PLAN BODY",
                  "do X instead", "AGENT FINDINGS", "chars omitted"):
            self.assertIn(s, text)
        self.assertNotIn("SIDECHAIN", text)
        self.assertLess(len(text), 4000)

    def test_unknown_lines_tolerated(self):
        self.transcript(["not json", line(type="weird"), user("hi")])
        self.assertEqual(transcript.digest(self.tp), ["[USER] hi"])

    def test_chunking(self):
        chunks = transcript.chunk(["a" * 1000] * 50, max_tokens=1000)
        self.assertTrue(all(len(c) <= 3600 for c in chunks))
        self.assertGreater(len(chunks), 10)

    def test_readiness(self):
        self.transcript([
            asst(None, ("TaskCreate", {"subject": "write tests"}), tool_id="a"),
            asst(None, ("TaskCreate", {"subject": "ship"}), tool_id="b"),
            asst(None, ("TaskUpdate", {"taskId": "1", "status": "completed"}), tool_id="c"),
            asst("Should I continue?"),
        ])
        r = transcript.readiness(self.tp)
        self.assertEqual(r["open_tasks"], ["ship"])
        self.assertTrue(r["pending_question"])


class TestMining(Base):
    def setUp(self):
        super().setUp()
        handoff.setup(self.ws, local_only=False)
        self.ws = config.Workspace(self.tmp)

    def test_no_compaction_means_no_mining(self):
        self.transcript([user("a"), asst("b")])
        self.assertEqual(handoff.mine(self.ws, self.data, self.sid)[0], "NO_COMPACTION")

    def test_mines_only_before_last_compaction_and_once(self):
        self.transcript([user("EARLY DECISION"), asst("ok"),
                         line(type="system", subtype="compact_boundary"),
                         line(type="user", isCompactSummary=True, message={"role": "user", "content": "summary"}),
                         user("LATE STUFF")])
        status, paths = handoff.mine(self.ws, self.data, self.sid)
        self.assertEqual(status, "CHUNKS")
        with open(paths[0], encoding="utf-8") as f:
            body = f.read()
        self.assertIn("EARLY DECISION", body)
        self.assertNotIn("LATE STUFF", body)
        self.assertTrue(paths[0].startswith(self.ws.handoffs_dir))
        handoff.mark_mined(self.ws, self.data, self.sid)
        self.assertIn(handoff.mine(self.ws, self.data, self.sid)[0], ("NO_COMPACTION", "NOTHING_NEW"))

    def test_force_all(self):
        self.transcript([user("ANYTHING")])
        status, paths = handoff.mine(self.ws, self.data, self.sid, force_all=True)
        self.assertEqual(status, "CHUNKS")


class TestHandoffLifecycle(Base):
    def test_setup_required_then_new_list_resolve_consume(self):
        self.assertEqual(handoff.ensure_setup(self.ws), "FIRST_HANDOFF_IN_WORKSPACE")
        handoff.setup(self.ws, local_only=True)
        ws = config.Workspace(self.tmp)
        self.assertIsNone(handoff.ensure_setup(ws))
        self.assertTrue(ws.configured)
        self.assertFalse(ws.initialized)  # no tree, handoffs still work
        f1, m1 = handoff.new(ws, "Parser Work!", self.sid)
        f2, m2 = handoff.new(ws, "Parser Work!", self.sid)
        self.assertNotEqual(m1["id"], m2["id"])
        writer.run(ws.handoffs_dir, "write", ["%s/HANDOFF.md" % m1["id"]], "# H\n")
        writer.run(ws.handoffs_dir, "write", ["%s/next-1-api.prompt.md" % m1["id"]], "do api\n")
        writer.run(ws.handoffs_dir, "write", ["%s/next-2-ui.prompt.md" % m1["id"]], "do ui\n")
        h, prompt, _ = handoff.resolve(ws, m1["id"] + ":2")
        self.assertEqual(prompt, "next-2-ui.prompt.md")
        self.assertEqual(len(handoff.list_handoffs(ws, only_open=True)), 2)
        handoff.set_status(ws, m1["id"], "consumed")
        self.assertEqual(len(handoff.list_handoffs(ws, only_open=True)), 1)


class TestCtxCleanPairing(Base):
    def setUp(self):
        super().setUp()
        handoff.setup(self.ws, local_only=False)
        self.ws = config.Workspace(self.tmp)
        f, m = handoff.new(self.ws, "x", self.sid)
        writer.run(self.ws.handoffs_dir, "write", ["%s/HANDOFF.md" % m["id"]], "# H\n")
        writer.run(self.ws.handoffs_dir, "write", ["%s/next-1-a.prompt.md" % m["id"]], "CONTINUE WITH STEP 3\n")
        self.hid = m["id"]

    def test_plain_clear_loads_nothing(self):
        self.hook("session-end", {"session_id": self.sid, "cwd": self.tmp, "reason": "clear"})
        out = self.hook("session-start", {"session_id": "new", "cwd": self.tmp, "source": "clear"})
        self.assertNotIn("hookSpecificOutput", out)

    def test_ctx_clean_reload_once_and_only_for_that_session(self):
        handoff.set_pending(self.ws, self.data, self.hid, self.sid)
        # another session in the same workspace clears first: must not get it
        self.hook("session-end", {"session_id": "other", "cwd": self.tmp, "reason": "clear"})
        self.assertNotIn("hookSpecificOutput", self.hook("session-start", {"session_id": "n0", "cwd": self.tmp, "source": "clear"}))
        self.hook("session-end", {"session_id": self.sid, "cwd": self.tmp, "reason": "clear"})
        out = self.hook("session-start", {"session_id": "n1", "cwd": self.tmp, "source": "clear"})
        self.assertIn("CONTINUE WITH STEP 3", out["hookSpecificOutput"]["additionalContext"])
        self.assertIn("loaded handoff", out["systemMessage"])
        out2 = self.hook("session-start", {"session_id": "n2", "cwd": self.tmp, "source": "clear"})
        self.assertNotIn("hookSpecificOutput", out2)
        self.assertEqual(handoff.list_handoffs(config.Workspace(self.tmp))[0]["status"], "consumed")

    def test_autoload_on_clear_opt_in(self):
        config.Workspace(self.tmp).save({"autoload_on_clear": True})
        self.hook("session-end", {"session_id": "zzz", "cwd": self.tmp, "reason": "clear"})
        out = self.hook("session-start", {"session_id": "n", "cwd": self.tmp, "source": "clear"})
        self.assertIn("experimental", out["systemMessage"])

    def test_startup_lists_open_handoffs(self):
        out = self.hook("session-start", {"session_id": "n", "cwd": self.tmp, "source": "startup"})
        self.assertIn("open handoff", out["systemMessage"])
        self.assertNotIn("hookSpecificOutput", out)  # zero tokens

    def test_precompact_checkpoint_and_pointer(self):
        self.transcript([user("IMPORTANT ANSWER"), asst("ok")])
        self.hook("pre-compact", {"session_id": self.sid, "cwd": self.tmp, "transcript_path": self.tp})
        cps = os.listdir(os.path.join(self.ws.handoffs_dir, ".checkpoints"))
        self.assertEqual(len(cps), 1)
        out = self.hook("session-start", {"session_id": self.sid, "cwd": self.tmp, "source": "compact"})
        self.assertIn(".checkpoints", out["hookSpecificOutput"]["additionalContext"])


class TestAdvisor(Base):
    def stop(self, tokens, last="Done.", sid=None):
        with open(self.tp, "a", encoding="utf-8") as f:
            f.write(asst("x", usage=usage(tokens)) + "\n")
        return self.hook("stop", {"session_id": sid or self.sid, "cwd": self.tmp, "transcript_path": self.tp,
                                  "last_assistant_message": last})

    def test_tail_usage(self):
        self.transcript([asst("a", usage=usage(5000)), asst("b", usage=usage(9000))])
        self.assertEqual(advisor.tail_usage(self.tp)[0], 9000)

    def test_thresholds_once_per_crossing_and_question_skip(self):
        self.transcript([])
        self.assertEqual(self.stop(50000), {})
        self.assertEqual(self.stop(130000, last="Shall I proceed?"), {})  # waiting on user: skip
        self.assertIn("Good point", self.stop(131000)["systemMessage"])
        self.assertEqual(self.stop(132000), {})  # not again immediately
        self.assertIn("Strongly", self.stop(210000)["systemMessage"])
        for _ in range(9):
            self.stop(211000)
        self.assertIn("systemMessage", self.stop(212000))  # repeats every 10 turns

    def test_off_and_subagent_ignored(self):
        self.transcript([])
        advisor.set_session(self.data, self.sid, False)
        self.assertEqual(self.stop(300000), {})
        out = self.hook("stop", {"session_id": "s2", "agent_id": "a1", "cwd": self.tmp, "transcript_path": self.tp})
        self.assertEqual(out, {})

    def test_prompt_nudge_once_per_level(self):
        self.transcript([asst("a", usage=usage(150000))])
        p = {"session_id": self.sid, "cwd": self.tmp, "transcript_path": self.tp, "prompt": "hi"}
        out = self.hook("prompt", p)
        self.assertIn("ctx-clean", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.hook("prompt", p), {})

    def test_billing_override(self):
        config.Workspace(self.tmp).save({"billing": "subscription"})
        self.assertEqual(advisor.billing(config.Workspace(self.tmp)), "subscription")


if __name__ == "__main__":
    unittest.main()

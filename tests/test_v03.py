"""Tests for v0.3: auto-commit, import scan, data-dir fallback, precise drift, path lint,
protected tags, skipped-step guard, nudge, zero-token notes."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "ctx-kit", "scripts"))

from ctxkit import autocommit, check, cli, config, feedback, handoff, hooks, importer, tree, writer  # noqa: E402

PY = "def alpha(x):\n    return x + 1\n\n\ndef beta(y):\n    return y * 2\n"
GIT = config.has_git()


def sh(cwd, *args):
    return subprocess.run(["git", "-c", "core.quotepath=off", "-c", "user.name=t", "-c", "user.email=t@t"] + list(args), cwd=cwd,
                          capture_output=True, text=True).stdout.strip()


@unittest.skipUnless(GIT, "git not installed")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ctxkit-v3-")
        self.data = os.path.join(self.tmp, ".data")
        os.environ["CLAUDE_PLUGIN_DATA"] = self.data
        os.makedirs(os.path.join(self.tmp, "src"))
        self.write("src/m.py", PY)
        sh(self.tmp, "init", "-q")
        sh(self.tmp, "add", "-A")
        sh(self.tmp, "commit", "-qm", "init")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["--root", self.tmp, "init", "--storage", "workspace", "--local-only", "y", "--nested-git", "y"])
        self.ws = config.Workspace(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def node(self, rel, body, sources="[src/m.py#alpha]", extra=""):
        writer.run(self.ws.ctx_root, "write", [rel],
                   "---\nsummary: s\nsources: %s\nstatus: draft\n%s---\n\n# T\n\n%s\n" % (sources, extra, body),
                   overwrite=True)

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


class TestAutoCommit(Base):
    def test_stop_hook_commits_with_generated_message(self):
        self.node("modules/m.md", "## Alpha\n\nalpha adds one.\n\n## Beta\n\nbeta doubles.")
        self.hook("stop", {"session_id": "sess12345678", "cwd": self.tmp})
        log = sh(self.ws.ctx_root, "log", "-1", "--format=%s%n%b")
        self.assertIn("ctx: ", log)
        self.assertIn("A modules/m.md", log)  # INDEX.md is refreshed in the same commit
        self.assertIn("session sess1234", log)
        # modify one section -> message names the section
        p = os.path.join(self.ws.ctx_root, "modules/m.md")
        with open(p, encoding="utf-8") as fh:
            text = fh.read().replace("beta doubles.", "beta doubles its input.")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.hook("stop", {"session_id": "sess12345678", "cwd": self.tmp})
        log = sh(self.ws.ctx_root, "log", "-1", "--format=%s%n%b")
        self.assertIn("ctx: update m", log)
        self.assertIn("sections: Beta", log)
        self.assertEqual(check.uncommitted(self.ws), [])

    def test_auto_commit_off(self):
        self.ws.save({"auto_commit": False})
        self.node("x.md", "text")
        self.hook("stop", {"session_id": "s", "cwd": self.tmp})
        self.assertTrue(check.uncommitted(config.Workspace(self.tmp)))

    def test_handoff_history(self):
        print_ = io.StringIO()
        msg = handoff.setup(self.ws, local_only=True, history=True)
        self.assertIn("nested git", msg)
        ws = config.Workspace(self.tmp)
        f, m = handoff.new(ws, "topic", "sess")
        writer.run(ws.handoffs_dir, "write", ["%s/HANDOFF.md" % m["id"]], "# H\n")
        os.makedirs(os.path.join(ws.handoffs_dir, ".work"), exist_ok=True)
        with open(os.path.join(ws.handoffs_dir, ".work", "junk.txt"), "w") as fh:
            fh.write("x")
        self.hook("stop", {"session_id": "sess", "cwd": self.tmp})
        files = sh(ws.handoffs_dir, "ls-files")
        self.assertIn("HANDOFF.md", files)
        self.assertNotIn("junk.txt", files)


class TestProtected(Base):
    def setUp(self):
        super().setUp()
        self.ws.save({"protected_tags": ["[SJ]"]})
        self.ws = config.Workspace(self.tmp)
        self.node("p.md", "- [SJ] keep the bridge single-threaded.\n- other line")
        tree.commit(self.ws, "base")

    def test_writer_refuses_then_allows(self):
        spec = "<<<<<<< OLD\n- [SJ] keep the bridge single-threaded.\n=======\n>>>>>>> NEW"
        with self.assertRaises(writer.WriteError):
            writer.run(self.ws.ctx_root, "replace", ["p.md"], spec, protected=["[SJ]"])
        writer.run(self.ws.ctx_root, "replace", ["p.md"], spec, protected=["[SJ]"], allow_protected=True)

    def test_tool_mode_removal_is_flagged_and_not_auto_committed(self):
        p = os.path.join(self.ws.ctx_root, "p.md")
        text = open(p, encoding="utf-8").read().replace("- [SJ] keep the bridge single-threaded.\n", "")
        open(p, "w", encoding="utf-8").write(text)
        lines, counts = check.run_check(self.ws, only=["p.md"])
        self.assertEqual(counts.get("protected"), 1)
        out = self.hook("stop", {"session_id": "s", "cwd": self.tmp})
        self.assertIn("held back", out["systemMessage"])
        self.assertIn("p.md", " ".join(check.uncommitted(self.ws)))
        # moving a tagged line (not removing) is fine
        text2 = open(p, encoding="utf-8").read() + "\n- [SJ] keep the bridge single-threaded.\n"
        open(p, "w", encoding="utf-8").write(text2)
        self.assertEqual(check.run_check(self.ws, only=["p.md"])[1].get("protected"), None)


class TestDriftAndLint(Base):
    def test_commit_attribution_and_diff(self):
        self.node("a.md", "alpha adds one.")
        check.stamp(self.ws, ["a.md"])
        self.write("src/m.py", PY.replace("x + 1", "x + 2"))
        sh(self.tmp, "commit", "-qam", "make alpha add two")
        lines, counts = check.run_check(self.ws, only=["a.md"], diff=True)
        text = "\n".join(lines)
        self.assertIn("by commits:", text)
        self.assertIn("make alpha add two", text)
        self.assertIn("+    return x + 2", text)
        self.write("src/m.py", PY.replace("x + 1", "x + 3"))
        self.assertIn("uncommitted changes", "\n".join(check.run_check(self.ws, only=["a.md"])[0]))

    def test_line_anchor_beyond_end(self):
        self.node("l.md", "see line", sources="[src/m.py#L2, src/m.py#L50]")
        lines, counts = check.run_check(self.ws, only=["l.md"])
        self.assertEqual(counts.get("missing"), 1)
        self.assertIn("beyond end of file", "\n".join(lines))
        check.stamp(self.ws, ["l.md"])
        self.write("src/m.py", PY.replace("return x + 1", "return x - 1"))
        self.assertEqual(check.run_check(self.ws, only=["l.md"])[1].get("drift"), 1)

    def test_backticked_paths(self):
        os.makedirs(os.path.join(self.tmp, "ui"))
        self.node("b.md", "Lives in `src/m.py` and `ui/registry_viewer`; run `ctx index`; glob `src/**`.")
        lines, counts = check.run_check(self.ws, only=["b.md"])
        self.assertEqual(counts.get("path"), 1)
        self.assertIn("ui/registry_viewer", "\n".join(lines))

    def test_edited_unverified_large(self):
        self.node("e.md", "original text")
        check.stamp(self.ws, ["e.md"])
        p = os.path.join(self.ws.ctx_root, "e.md")
        open(p, "a", encoding="utf-8").write("\nadded later without stamping\n")
        self.assertEqual(check.run_check(self.ws, only=["e.md"])[1].get("edited"), 1)
        writer.run(self.ws.ctx_root, "write", ["u.md"], "---\nsummary: u\nstatus: current\n---\n\n# U\n")
        self.assertEqual(check.run_check(self.ws, only=["u.md"])[1].get("unverified"), 1)
        writer.run(self.ws.ctx_root, "write", ["big.md"], "---\nsummary: b\n---\n\n" + "word " * 4000)
        self.assertEqual(check.run_check(self.ws, only=["big.md"])[1].get("large"), 1)


class TestNudgeAndNotes(Base):
    def test_update_nudge_once(self):
        self.ws.save({"update_nudge_edits": 3})
        sid = "nudge-sess"
        self.hook("session-start", {"session_id": sid, "cwd": self.tmp, "source": "startup"})
        import time
        time.sleep(0.05)
        for i in range(3):
            self.hook("post-edit", {"session_id": sid, "cwd": self.tmp, "tool_input": {"file_path": "src/f%d.py" % i}})
        out = self.hook("stop", {"session_id": sid, "cwd": self.tmp})
        self.assertIn("context tree wasn't updated", out.get("systemMessage", ""))
        out2 = self.hook("stop", {"session_id": sid, "cwd": self.tmp})
        self.assertNotIn("context tree wasn't updated", out2.get("systemMessage", ""))

    def test_no_nudge_when_tree_updated(self):
        self.ws.save({"update_nudge_edits": 2})
        sid = "nudge2"
        self.hook("session-start", {"session_id": sid, "cwd": self.tmp, "source": "startup"})
        import time
        time.sleep(0.05)
        for i in range(2):
            self.hook("post-edit", {"session_id": sid, "cwd": self.tmp, "tool_input": {"file_path": "src/g%d.py" % i}})
        self.node("fresh.md", "updated")
        out = self.hook("stop", {"session_id": sid, "cwd": self.tmp})
        self.assertNotIn("wasn't updated", out.get("systemMessage", ""))

    def test_notes_are_blocked_from_claude(self):
        out = self.hook("prompt", {"session_id": "s", "cwd": self.tmp, "prompt": "ctx-note: advisor line didn't show in VS Code at %s" % self.tmp})
        self.assertEqual(out["decision"], "block")
        self.assertIn("note #1 saved", out["reason"])
        self.assertNotIn("hookSpecificOutput", out)
        out = self.hook("prompt", {"session_id": "s", "cwd": self.tmp, "prompt": "ctx-notes"})
        self.assertIn("=== ctx-kit feedback", out["reason"])
        self.assertIn("1. [note", out["reason"])
        self.assertNotIn(self.tmp, out["reason"])
        self.hook("prompt", {"session_id": "s", "cwd": self.tmp, "prompt": "ctx-notes clear"})
        self.assertEqual(feedback.notes(self.data), [])
        self.assertEqual(self.hook("prompt", {"session_id": "s", "cwd": self.tmp, "prompt": "normal question"}), {})


class TestReviewFixes(Base):
    """Regression tests for the independent review of v0.3."""

    def test_non_ascii_names_and_emoji(self):
        self.ws.save({"protected_tags": ["[SJ]"]})
        ws = config.Workspace(self.tmp)
        writer.run(ws.ctx_root, "write", ["café.md"], "---\nsummary: c\n---\n\n# Café ❌ 📁\n\n- [SJ] keep ❌ here\n")
        self.hook("stop", {"session_id": "s", "cwd": self.tmp})
        self.assertIn("café.md", sh(ws.ctx_root, "ls-files"))
        self.assertIn("café", sh(ws.ctx_root, "log", "-1", "--format=%s"))
        p = os.path.join(ws.ctx_root, "café.md")
        with open(p, encoding="utf-8") as fh:
            t = fh.read()
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(t.replace("- [SJ] keep ❌ here\n", ""))
        out = self.hook("stop", {"session_id": "s", "cwd": self.tmp})
        self.assertIn("held back", out["systemMessage"])
        self.assertEqual(check.run_check(ws, only=["café.md"])[1].get("protected"), 1)

    def test_held_back_warns_once_with_fix(self):
        self.ws.save({"protected_tags": ["[SJ]"]})
        ws = config.Workspace(self.tmp)
        self.node("p.md", "- [SJ] rule one")
        tree.commit(ws, "base")
        p = os.path.join(ws.ctx_root, "p.md")
        with open(p, encoding="utf-8") as fh:
            t = fh.read()
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(t.replace("- [SJ] rule one", "- rule one (edited)"))
        out = self.hook("stop", {"session_id": "s", "cwd": self.tmp})
        self.assertIn("--allow-protected", out["systemMessage"])
        out2 = self.hook("stop", {"session_id": "s", "cwd": self.tmp})
        self.assertNotIn("held back", out2.get("systemMessage", ""))
        print_ = tree.commit(config.Workspace(self.tmp), "approved", allow_protected=True, data=self.data)
        self.assertIn("commit: ctx", print_)
        self.assertEqual(check.uncommitted(config.Workspace(self.tmp)), [])

    def test_moving_tagged_line_to_other_node_is_allowed(self):
        self.ws.save({"protected_tags": ["[SJ]"]})
        ws = config.Workspace(self.tmp)
        self.node("big.md", "- [SJ] keep this rule\n- other")
        tree.commit(ws, "base")
        writer.run(ws.ctx_root, "write", ["split.md"], "---\nsummary: s\n---\n\n- [SJ] keep this rule\n")
        writer.run(ws.ctx_root, "replace", ["big.md"], "<<<<<<< OLD\n- [SJ] keep this rule\n=======\n>>>>>>> NEW",
                   protected=["[SJ]"])
        out = self.hook("stop", {"session_id": "s", "cwd": self.tmp})
        self.assertNotIn("held back", out.get("systemMessage", ""))
        self.assertEqual(check.uncommitted(config.Workspace(self.tmp)), [])

    def test_path_lint_ignores_non_paths(self):
        self.node("n.md", "Send `application/json`, `and/or` use `owner/repo` on `feature/foo`; see `docs/guide.md` and `src/gone.py` and `src/m.py`.")
        lines, counts = check.run_check(self.ws, only=["n.md"])
        flagged = "\n".join(l for l in lines if l.startswith("PATH"))
        self.assertEqual(counts.get("path"), 2, flagged)
        self.assertIn("docs/guide.md", flagged)
        self.assertIn("src/gone.py", flagged)

    def test_stale_lock_removed_and_failure_reported(self):
        self.node("a.md", "x")
        lock = os.path.join(self.ws.ctx_root, ".git", "index.lock")
        open(lock, "w").close()
        os.utime(lock, (1, 1))
        out = self.hook("stop", {"session_id": "s", "cwd": self.tmp})
        self.assertIn("stale git lock", out["systemMessage"])
        self.assertEqual(check.uncommitted(config.Workspace(self.tmp)), [])
        self.node("b.md", "y")
        open(lock, "w").close()  # fresh lock -> commit fails -> reported once
        out = self.hook("stop", {"session_id": "s2", "cwd": self.tmp})
        self.assertIn("NOT being recorded", out["systemMessage"])
        os.remove(lock)

    def test_nested_git_false_is_honoured_and_trash_ignored(self):
        writer.run(self.ws.ctx_root, "write", ["t.md"], "x")
        writer.run(self.ws.ctx_root, "rm", ["t.md"], "")
        self.hook("stop", {"session_id": "s", "cwd": self.tmp})
        self.assertNotIn(".trash", sh(self.ws.ctx_root, "ls-files"))
        self.ws.save({"nested_git": False})
        self.node("z.md", "z")
        self.assertEqual(autocommit.roots(config.Workspace(self.tmp)), [])

    def test_legacy_current_nodes_downgraded(self):
        writer.run(self.ws.ctx_root, "write", ["old.md"], "---\nsummary: o\nstatus: current\nlast_verified: 2026-10-01\n---\n\n# Old\n")
        self.node("new.md", "fresh")
        check.stamp(self.ws, ["new.md"])
        self.assertEqual(check.mark_legacy_draft(self.ws), ["old.md"])

    def test_doc_duplicates(self):
        sent = "The bridge talks to the Perl tool over stdin and stdout using one JSON message per line."
        self.node("d.md", sent)
        self.write("README.md", "# Readme\n\n" + sent + "\n")
        lines, counts = check.run_check(self.ws, docs=True)
        self.assertEqual(counts.get("dup-doc"), 1)

    def test_scrub_collapses_whole_paths(self):
        out = feedback._scrub_paths("at %s/src/secret_project.py and C:\\Users\\bob\\Acme\\x.py" % self.tmp, self.ws)
        self.assertNotIn("secret_project", out)
        self.assertNotIn("Acme", out)


class TestImportAndData(Base):
    def test_import_scan(self):
        self.write("CLAUDE.md", "# Project\n\nIntro.\n\n## Protocols\n\nLong text.\n\n@docs/arch.md\n\n## Style\n\nUse `@decorators`.\n")
        self.write("docs/arch.md", "# Arch\n\nStuff.\n")
        out = importer.scan(config.Workspace(self.tmp), ["docs"])
        self.assertIn("## CLAUDE.md  [every session", out)
        self.assertIn("@imports: docs/arch.md", out)
        self.assertNotIn("decorators", out.split("@imports:")[1].split("\n")[0])
        self.assertIn("docs/arch.md  [doc", out)
        self.assertIn("Protocols", out)

    def test_data_dir_fallback_finds_plugin_folder(self):
        home = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(home, "plugins", "data", "ctx-kit-sambhav-plugins"))
            env = dict(os.environ)
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
            os.environ["CLAUDE_CONFIG_DIR"] = home
            self.assertTrue(config.plugin_data_dir().endswith("ctx-kit-sambhav-plugins"))
        finally:
            os.environ.clear()
            os.environ.update(env)
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

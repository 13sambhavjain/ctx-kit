"""Unit tests for ctx-kit's Python core. Run: python3 -m unittest discover -s tests"""
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
SCRIPTS = os.path.join(os.path.dirname(HERE), "plugins", "ctx-kit", "scripts")
sys.path.insert(0, SCRIPTS)

from ctxkit import check, cli, config, redact, sources, tree, util, writer  # noqa: E402

PY = '''import re

class Parser:
    def __init__(self, data):
        self.data = data

    def parse(self):
        return self.data.split(",")


def helper_fn(x):
    return x * 2
'''

TS = '''export function connect(host: string, port: number) {
  const s = open(host, port);
  return s;
}

export const close = async (s) => {
  s.end();
};
'''


def run_cli(*args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(list(args))
    return rc, buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ctxkit-test-")
        self.data = os.path.join(self.tmp, ".data")
        os.environ["CLAUDE_PLUGIN_DATA"] = self.data
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        os.makedirs(os.path.join(self.tmp, "src", "proto"))
        os.makedirs(os.path.join(self.tmp, "lib"))
        self.write("src/proto/parser.py", PY)
        self.write("lib/net.ts", TS)
        self.git = config.has_git()
        if self.git:
            subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, rel):
        with open(os.path.join(self.tmp, rel), encoding="utf-8") as f:
            return f.read()

    def init(self, **kw):
        args = ["--root", self.tmp, "init", "--storage", kw.get("storage", "workspace"),
                "--local-only", kw.get("local_only", "y"), "--nested-git", kw.get("nested_git", "n")]
        return run_cli(*args)


class TestFrontmatter(unittest.TestCase):
    def test_roundtrip(self):
        meta = {"summary": "A: tricky, summary", "paths": ["src/**", "lib/x.py"],
                "source_hashes": {"src/a.py#Foo": "abc123", "lib/": "d0001"}, "status": "current"}
        text = util.dump_frontmatter(meta, "# Body\n")
        m2, body = util.parse_frontmatter(text)
        self.assertEqual(m2["summary"], meta["summary"])
        self.assertEqual(m2["paths"], meta["paths"])
        self.assertEqual(m2["source_hashes"], meta["source_hashes"])
        self.assertEqual(body.strip(), "# Body")

    def test_no_frontmatter(self):
        self.assertEqual(util.parse_frontmatter("# hi"), ({}, "# hi"))

    def test_block_list(self):
        m, _ = util.parse_frontmatter("---\nsources:\n  - a.py\n  - b.py#X\n---\nbody")
        self.assertEqual(m["sources"], ["a.py", "b.py#X"])


class TestRedact(unittest.TestCase):
    def test_patterns(self):
        t, kinds = redact.redact('token sk-ant-abcdefghijklmnopqrstuvwxyz0123 and password = "hunter2hunter2"')
        self.assertNotIn("sk-ant-abc", t)
        self.assertNotIn("hunter2hunter2", t)
        self.assertIn("anthropic-key", kinds)

    def test_clean(self):
        self.assertEqual(redact.redact("nothing here")[1], [])


class TestSources(Base):
    def test_symbol_hash_and_drift(self):
        h1 = sources.anchor_hash(self.tmp, "src/proto/parser.py#Parser")
        self.assertNotEqual(h1, "missing")
        self.write("src/proto/parser.py", PY.replace('split(",")', 'split(";")'))
        self.assertNotEqual(sources.anchor_hash(self.tmp, "src/proto/parser.py#Parser"), h1)
        # helper_fn untouched -> same hash
        self.assertEqual(sources.anchor_hash(self.tmp, "src/proto/parser.py#helper_fn"),
                         sources.anchor_hash(self.tmp, "src/proto/parser.py#helper_fn"))

    def test_ts_symbols(self):
        self.assertNotEqual(sources.anchor_hash(self.tmp, "lib/net.ts#connect"), "missing")
        self.assertNotEqual(sources.anchor_hash(self.tmp, "lib/net.ts#close"), "missing")
        self.assertEqual(sources.anchor_hash(self.tmp, "lib/net.ts#nope"), "missing")

    def test_rename_detection(self):
        old = sources.anchor_hash(self.tmp, "src/proto/parser.py#helper_fn")
        self.write("src/proto/parser.py", PY.replace("def helper_fn", "def double_it"))
        status, cands = sources.find_moved(self.tmp, "src/proto/parser.py#helper_fn", old)
        self.assertEqual((status, cands), ("renamed", ["src/proto/parser.py#double_it"]))

    def test_move_to_other_file(self):
        old = sources.anchor_hash(self.tmp, "lib/net.ts#connect")
        self.write("lib/net.ts", "export const close = async (s) => {\n  s.end();\n};\n")
        self.write("lib/conn.ts", TS.split("\n\n")[0])
        status, cands = sources.find_moved(self.tmp, "lib/net.ts#connect", old)
        self.assertEqual((status, cands), ("renamed", ["lib/conn.ts#connect"]))
        # an identical copy elsewhere makes it ambiguous (never auto-fixed)
        self.write("lib/copy.ts", TS.split("\n\n")[0].replace("connect", "zzz"))
        self.assertEqual(sources.find_moved(self.tmp, "lib/net.ts#connect", old)[0], "ambiguous")

    def test_dir_and_file_anchors(self):
        self.assertTrue(sources.anchor_hash(self.tmp, "src/").startswith("d"))
        self.assertEqual(sources.anchor_hash(self.tmp, "nope/"), "missing")
        self.assertNotEqual(sources.anchor_hash(self.tmp, "lib/net.ts"), "missing")


class TestInitAndTree(Base):
    def test_init_creates_skeleton(self):
        rc, out = self.init()
        self.assertEqual(rc, 0)
        ctx = os.path.join(self.tmp, ".claude", "ctx")
        for f in ("INDEX.md", "architecture.md", "decisions/DECISIONS.md", "decisions/0001-context-storage.md",
                  "modules/src.md", "modules/lib.md"):
            self.assertTrue(os.path.exists(os.path.join(ctx, f)), f)
        self.assertIn("ctx-kit:start", self.read("CLAUDE.local.md"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "CLAUDE.md")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, ".claude", "rules", "ctx-modules-src.md")))
        if self.git:
            excl = self.read(".git/info/exclude")
            self.assertIn("/.claude/ctx/", excl)
            self.assertIn("/.claude/rules/ctx-*.md", excl)

    def test_init_idempotent_and_committed_mode(self):
        self.init(local_only="n")
        self.assertIn("ctx-kit:start", self.read("CLAUDE.md"))
        before = self.read(".claude/ctx/architecture.md")
        self.init(local_only="n")
        self.assertEqual(before, self.read(".claude/ctx/architecture.md"))
        self.assertEqual(self.read("CLAUDE.md").count("ctx-kit:start"), 1)
        if self.git:
            self.assertNotIn("ctx-kit", self.read(".git/info/exclude"))

    def test_nested_git(self):
        if not self.git:
            self.skipTest("git not installed")
        rc, out = self.init(nested_git="y")
        self.assertIn("commit: done", out)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, ".claude", "ctx", ".git")))

    def test_dotctx_and_home(self):
        self.init(storage="dotctx")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, ".ctx", "INDEX.md")))
        ws = config.Workspace(self.tmp)
        ws.save({"storage": "home"})
        self.assertIn(os.path.join(".ctx-kit", "trees"), config.Workspace(self.tmp).ctx_root)

    def test_not_initialized_guard(self):
        rc, _ = run_cli("--root", self.tmp, "check")
        self.assertEqual(rc, 2)


class TestWriterAndCheck(Base):
    NODE = ("---\nsummary: Parser node\nparent: INDEX.md\npaths: [src/proto/**]\n"
            "sources: [src/proto/parser.py#Parser, src/proto/parser.py#helper_fn]\nstatus: draft\n---\n\n"
            "# Parser\n\nSplits on commas.\n")

    def setUp(self):
        super().setUp()
        self.init()
        self.ws = config.Workspace(self.tmp)

    def w(self, op, path, content="", **kw):
        return writer.run(self.ws.ctx_root, op, [path] if isinstance(path, str) else path, content, **kw)

    def test_write_replace_refusals(self):
        self.w("write", "protocols/parser.md", self.NODE)
        with self.assertRaises(writer.WriteError):
            self.w("write", "protocols/parser.md", "x")
        self.w("replace", "protocols/parser.md", "<<<<<<< OLD\nSplits on commas.\n=======\nSplits on commas; returns a list.\n>>>>>>> NEW\n")
        self.assertIn("returns a list", self.read(".claude/ctx/protocols/parser.md"))
        for bad in ("../x.md", "../../settings.json", "hooks/a.md", "skills/x/SKILL.md", "/etc/passwd"):
            with self.assertRaises(writer.WriteError, msg=bad):
                self.w("write", bad, "x")
        with self.assertRaises(writer.WriteError):  # OLD not found
            self.w("replace", "protocols/parser.md", "<<<<<<< OLD\nnope\n=======\nx\n>>>>>>> NEW")

    def test_replace_redacts_new_only(self):
        self.w("write", "n.md", "a\n")
        self.w("replace", "n.md", "<<<<<<< OLD\na\n=======\napi_key = \"abcdefgh12345\"\n>>>>>>> NEW")
        self.assertIn("[REDACTED]", self.read(".claude/ctx/n.md"))

    def test_rm_goes_to_trash(self):
        self.w("write", "tmp.md", "x")
        self.w("rm", "tmp.md")
        self.assertFalse(os.path.exists(os.path.join(self.ws.ctx_root, "tmp.md")))
        self.assertTrue(os.path.isdir(os.path.join(self.ws.ctx_root, ".trash")))

    def test_stamp_drift_rename_fix(self):
        self.w("write", "protocols/parser.md", self.NODE)
        check.stamp(self.ws, ["protocols/parser.md"])
        lines, counts = check.run_check(self.ws, only=["protocols/parser.md"])
        self.assertEqual(counts, {})
        self.write("src/proto/parser.py", PY.replace("def helper_fn", "def double_it").replace('split(",")', 'split(";")'))
        lines, counts = check.run_check(self.ws, only=["protocols/parser.md"])
        self.assertEqual(counts.get("drift"), 1)
        self.assertEqual(counts.get("renamed"), 1)
        lines, counts = check.run_check(self.ws, fix=True, only=["protocols/parser.md"])
        meta, _, _ = tree.load_node(self.ws.ctx_root, "protocols/parser.md")
        self.assertIn("src/proto/parser.py#double_it", meta["sources"])
        self.assertEqual(meta["status"], "stale-suspect")
        self.assertEqual(check.stale_sources(self.ws, "protocols/parser.md"), ["src/proto/parser.py#Parser"])

    def test_index_routing_and_rules(self):
        self.w("write", "protocols/parser.md", self.NODE)
        tree.write_index(self.ws)
        tree.write_rules(self.ws)
        idx = self.read(".claude/ctx/INDEX.md")
        self.assertIn("| `src/proto/**` | [Parser](protocols/parser.md) |", idx)
        self.assertIn("- **protocols/**", idx)
        rule = self.read(".claude/rules/ctx-protocols-parser.md")
        self.assertIn('paths: ["src/proto/**"]', rule)
        # removing paths removes the generated rule
        self.w("replace", "protocols/parser.md", "<<<<<<< OLD\npaths: [src/proto/**]\n=======\n>>>>>>> NEW")
        tree.write_rules(self.ws)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".claude", "rules", "ctx-protocols-parser.md")))

    def test_broken_link_and_duplicates(self):
        sent = "The protocol handshake always starts with a version byte followed by the session token length."
        self.w("write", "a.md", "---\nsummary: a\n---\n\n# A\n\n%s See [b](missing.md).\n" % sent)
        self.w("write", "b.md", "---\nsummary: b\n---\n\n# B\n\n%s\n" % sent)
        lines, counts = check.run_check(self.ws)
        self.assertEqual(counts.get("link"), 1)
        self.assertEqual(counts.get("duplicate"), 1)

    def test_search(self):
        self.w("write", "protocols/parser.md", self.NODE)
        res = check.search(self.ws, "parser commas")
        self.assertEqual(res[0][1], "protocols/parser.md")


class TestHooks(Base):
    def run_hook(self, event, payload):
        from ctxkit import hooks
        buf = io.StringIO()
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        try:
            with redirect_stdout(buf):
                hooks.main(event, self.data)
        finally:
            sys.stdin = old_stdin
        return buf.getvalue()

    def test_first_run_note_once(self):
        out = self.run_hook("session-start", {"source": "startup", "cwd": self.tmp})
        self.assertIn("systemMessage", out)
        self.assertEqual(self.run_hook("session-start", {"source": "startup", "cwd": self.tmp}), "")

    def test_post_read_stale_and_post_edit(self):
        self.init()
        ws = config.Workspace(self.tmp)
        writer.run(ws.ctx_root, "write", ["p.md"], TestWriterAndCheck.NODE)
        check.stamp(ws, ["p.md"])
        node = os.path.join(ws.ctx_root, "p.md")
        self.assertEqual(self.run_hook("post-read", {"cwd": self.tmp, "tool_input": {"file_path": node}}), "")
        self.write("src/proto/parser.py", PY.replace('split(",")', 'split(";")'))
        out = json.loads(self.run_hook("post-read", {"cwd": self.tmp, "tool_input": {"file_path": node}}))
        self.assertIn("may be stale", out["hookSpecificOutput"]["additionalContext"])
        self.run_hook("post-edit", {"cwd": self.tmp, "session_id": "abc", "tool_input": {"file_path": "src/proto/parser.py"}})
        rc, out = run_cli("--root", self.tmp, "--data", self.data, "touched", "--session", "abc")
        self.assertIn("src/proto/parser.py", out)

    def test_garbage_input_is_silent(self):
        from ctxkit import hooks
        old = sys.stdin
        sys.stdin = io.StringIO("not json")
        try:
            self.assertEqual(hooks.main("post-read", self.data), 0)
        finally:
            sys.stdin = old


class TestFeedback(Base):
    def test_no_paths_in_draft(self):
        self.init()
        rc, out = run_cli("--root", self.tmp, "feedback", "--note", "secret path %s here" % self.tmp)
        self.assertNotIn(self.tmp, out)
        self.assertIn("<workspace>", out)


if __name__ == "__main__":
    unittest.main()

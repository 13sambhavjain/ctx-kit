"""Hook handlers. Every handler must be fast, silent on error, and exit 0.

Output conventions (Claude Code hooks):
  systemMessage                      -> shown to the user only (zero tokens)
  hookSpecificOutput.additionalContext -> added to Claude's context (costs tokens; used sparingly)
"""
import json
import os
import sys
import time

from . import check, config, util


def _out(obj):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def _sessions_dir(data):
    return os.path.join(data, "sessions")


def _prune(data, days=14):
    d = _sessions_dir(data)
    if not os.path.isdir(d):
        return
    cutoff = time.time() - days * 86400
    for name in os.listdir(d):
        p = os.path.join(d, name)
        try:
            if os.path.getmtime(p) < cutoff:
                for f in os.listdir(p):
                    os.remove(os.path.join(p, f))
                os.rmdir(p)
        except OSError:
            pass


def session_start(inp, data):
    cwd = inp.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    ws = config.Workspace(cwd)
    _prune(data)
    if not ws.initialized:
        seen_path = os.path.join(data, "seen-workspaces.json")
        seen = util.load_json(seen_path, {}) or {}
        key = os.path.normcase(ws.root)
        if key in seen or inp.get("source") not in (None, "startup"):
            return
        seen[key] = util.today()
        util.save_json(seen_path, seen)
        _out({"systemMessage": "ctx-kit: no context tree here yet. Run /ctx-kit:ctx init to set one up "
                               "(this note is shown once per workspace)."})
        return
    msgs = []
    if ws.get("write_mode") == "tool" and inp.get("source") in (None, "startup", "resume"):
        msgs.append("ctx-kit: when Claude asks to edit files in .claude/, choose \"allow for this session\" "
                    "to avoid repeated prompts.")
    if msgs:
        _out({"systemMessage": " ".join(msgs)})


def post_read(inp, data):
    """Warn (one line) when Claude reads a ctx node whose sources changed since last stamp."""
    ti = inp.get("tool_input") or {}
    fp = ti.get("file_path") or ""
    if not fp.endswith(".md"):
        return
    cwd = inp.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    ws = config.Workspace(cwd)
    if not ws.initialized:
        return
    root = os.path.normcase(os.path.abspath(ws.ctx_root))
    full = os.path.normcase(os.path.abspath(fp if os.path.isabs(fp) else os.path.join(cwd, fp)))
    if not full.startswith(root + os.sep):
        return
    relnode = util.rel(full, root)
    bad = check.stale_sources(ws, relnode)
    if not bad:
        return
    shown = ", ".join(bad[:5]) + (" …" if len(bad) > 5 else "")
    _out({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": "ctx-kit: this context node may be stale: sources changed since it was last verified (%s). "
                             "Verify against the code before relying on it; after correcting, run ctx stamp on it." % shown}})


def post_edit(inp, data):
    """Record files this session changed (outside the repo, in plugin data). No output."""
    cwd = inp.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    ws = config.Workspace(cwd)
    if not ws.initialized:
        return
    ti = inp.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("notebook_path")
    sid = inp.get("session_id") or "unknown"
    if not fp:
        return
    d = os.path.join(_sessions_dir(data), "".join(c for c in sid if c.isalnum() or c in "-_")[:64])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "touched.txt"), "a", encoding="utf-8") as f:
        f.write("%s\t%s\n" % (util.now_iso(), util.rel(os.path.abspath(os.path.join(cwd, fp)), ws.root)))


HANDLERS = {
    "session-start": session_start,
    "post-read": post_read,
    "post-edit": post_edit,
}


def main(event, data_dir):
    data = config.plugin_data_dir(data_dir)
    try:
        raw = sys.stdin.read()
        inp = json.loads(raw) if raw.strip() else {}
    except Exception:
        inp = {}
    try:
        h = HANDLERS.get(event)
        if h:
            h(inp, data)
    except Exception:
        util.log_error(data, "hook " + event)
    return 0

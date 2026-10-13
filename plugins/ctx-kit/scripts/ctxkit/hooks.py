"""Hook handlers. Every handler must be fast, silent on error, and exit 0.

Output conventions (Claude Code hooks):
  systemMessage                        -> shown to the user only (zero tokens)
  hookSpecificOutput.additionalContext -> added to Claude's context (costs tokens; used sparingly)
"""
import json
import os
import re
import sys
import time

from . import advisor, autocommit, check, config, feedback, handoff, tree, upgrade, util


def _out(obj):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def _sessions_dir(data):
    return os.path.join(data, "sessions")


def _cwd(inp):
    return inp.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def _prune(data, days=14):
    cutoff = time.time() - days * 86400
    for sub in ("sessions", "advisor", "pending"):
        d = os.path.join(data, sub)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            p = os.path.join(d, name)
            try:
                if os.path.getmtime(p) >= cutoff:
                    continue
                if os.path.isdir(p):
                    for f in os.listdir(p):
                        os.remove(os.path.join(p, f))
                    os.rmdir(p)
                else:
                    os.remove(p)
            except OSError:
                pass


def session_start(inp, data):
    cwd = _cwd(inp)
    ws = config.Workspace(cwd)
    source = inp.get("source") or "startup"
    _prune(data)
    sid = inp.get("session_id")
    if sid:
        util.save_json(os.path.join(data, "sessions", _safe_sid(sid), "start.json"), {"t": time.time()})
    msgs, ctx = [], []

    if source == "clear":
        pend = handoff.take_pending_after_clear(ws, data, cwd)
        if pend:
            ctx.append(handoff.prompt_payload(pend, pend.get("prompt")))
            handoff.set_status(ws, pend.get("handoff"), "consumed")
            msgs.append("ctx-kit: loaded handoff %s into this fresh session." % pend.get("handoff"))
        elif ws.get("autoload_on_clear"):
            h, prompt, _ = handoff.resolve(ws, "")
            if h and prompt:
                ctx.append(handoff.prompt_payload(h, os.path.join(h["folder"], prompt)))
                handoff.set_status(ws, h["id"], "consumed")
                msgs.append("ctx-kit (experimental autoload_on_clear): loaded latest open handoff %s." % h["id"])
    elif source == "compact" and ws.configured:
        st = util.load_json(os.path.join(data, "last-checkpoint.json"), {}) or {}
        if st.get("session") == inp.get("session_id") and st.get("path") and os.path.exists(st["path"]):
            ctx.append("ctx-kit: before this compaction a full digest of the session was saved to %s. "
                       "If the summary lacks a detail you need (a decision, reason or answer), read that file." % st["path"])
    elif source == "startup":
        if not ws.configured:
            seen_path = os.path.join(data, "seen-workspaces.json")
            seen = util.load_json(seen_path, {}) or {}
            key = os.path.normcase(ws.root)
            if key not in seen:
                seen[key] = util.today()
                util.save_json(seen_path, seen)
                msgs.append("ctx-kit: no context tree here yet. Run /ctx-kit:ctx init to set one up "
                            "(this note is shown once per workspace).")
        else:
            if upgrade.needs_notice(ws) and upgrade.hints(ws):
                from . import __version__
                key = os.path.normcase(ws.root) + "@" + __version__
                seen_path = os.path.join(data, "upgrade-noticed.json")
                seen = util.load_json(seen_path, {}) or {}
                if key not in seen:
                    seen[key] = util.today()
                    util.save_json(seen_path, seen)
                    msgs.append("ctx-kit updated to %s: this project has %d recommended change(s). "
                                "Run /ctx-kit:ctx upgrade to review them." % (__version__, len(upgrade.hints(ws))))
            opens = handoff.list_handoffs(ws, only_open=True)
            if opens:
                ids = ", ".join(h["id"] for h in opens[:3])
                msgs.append("ctx-kit: %d open handoff(s): %s. Continue one with /ctx-kit:pickup <id>." % (len(opens), ids))

    if ws.initialized and ws.get("write_mode") == "tool" and source in ("startup", "resume"):
        msgs.append("ctx-kit: when Claude asks to edit files in .claude/, choose \"allow for this session\" "
                    "to avoid repeated prompts.")
    out = {}
    if msgs:
        out["systemMessage"] = " ".join(msgs)
    if ctx:
        out["hookSpecificOutput"] = {"hookEventName": "SessionStart", "additionalContext": "\n\n".join(ctx)}
    if out:
        _out(out)


def _safe_sid(sid):
    return "".join(c for c in (sid or "unknown") if c.isalnum() or c in "-_")[:64] or "unknown"


def session_end(inp, data):
    reason = inp.get("reason") or inp.get("source") or ""
    if reason == "clear":
        handoff.record_clear(data, inp.get("session_id") or "", _cwd(inp))
    ws = config.Workspace(_cwd(inp))
    if ws.configured:
        autocommit.run(ws, inp.get("session_id") or "", data=data)


def pre_compact(inp, data):
    ws = config.Workspace(_cwd(inp))
    if not ws.configured:
        return
    autocommit.run(ws, inp.get("session_id") or "", data=data)
    p = handoff.checkpoint(ws, data, inp.get("session_id") or "", inp.get("transcript_path"))
    if p:
        util.save_json(os.path.join(data, "last-checkpoint.json"), {"session": inp.get("session_id"), "path": p})


def post_read(inp, data):
    """Warn (one line) when Claude reads a ctx node whose sources changed since last stamp."""
    ti = inp.get("tool_input") or {}
    fp = ti.get("file_path") or ""
    if not fp.endswith(".md"):
        return
    cwd = _cwd(inp)
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
    cwd = _cwd(inp)
    ws = config.Workspace(cwd)
    if not ws.initialized:
        return
    ti = inp.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("notebook_path")
    sid = inp.get("session_id") or "unknown"
    if not fp:
        return
    d = os.path.join(_sessions_dir(data), _safe_sid(sid))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "touched.txt"), "a", encoding="utf-8") as f:
        f.write("%s\t%s\n" % (util.now_iso(), util.rel(os.path.abspath(os.path.join(cwd, fp)), ws.root)))


def _tree_updated_since(ws, t0):
    for root in (ws.ctx_root,):
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d != ".git"]
            for f in fns:
                try:
                    if os.path.getmtime(os.path.join(dp, f)) > t0:
                        return True
                except OSError:
                    pass
    return False


def update_nudge(ws, data, sid):
    """Once per session: many code edits but the tree wasn't touched -> one line to the user."""
    limit = int(ws.get("update_nudge_edits") or 0)
    if not ws.initialized or limit <= 0 or not sid:
        return None
    d = os.path.join(_sessions_dir(data), _safe_sid(sid))
    if os.path.exists(os.path.join(d, "nudged")):
        return None
    p = os.path.join(d, "touched.txt")
    if not os.path.exists(p):
        return None
    tree_rel = util.rel(ws.ctx_root, ws.root) + "/"
    files = set()
    for line in util.read_text(p).splitlines():
        f = line.split("\t", 1)[-1]
        if not f.startswith((tree_rel, ".claude/")):
            files.add(f)
    if len(files) < limit:
        return None
    t0 = (util.load_json(os.path.join(d, "start.json"), {}) or {}).get("t", 0)
    if _tree_updated_since(ws, t0):
        return None
    util.atomic_write(os.path.join(d, "nudged"), util.now_iso())
    return ("ctx-kit: %d code files changed this session but the context tree wasn't updated. "
            "If you made decisions or learned facts worth keeping, run /ctx-kit:ctx update." % len(files))


def stop(inp, data):
    if inp.get("agent_id"):  # subagent stop events are not the user's turn
        return
    ws = config.Workspace(_cwd(inp))
    msgs = []
    if ws.configured:
        if ws.initialized:
            tree.write_index(ws)  # keeps INDEX in sync even if a step was skipped (zero tokens)
        _, warns = autocommit.run(ws, inp.get("session_id") or "", data=data)
        msgs += ["ctx-kit: " + w for w in warns]
        n = update_nudge(ws, data, inp.get("session_id"))
        if n:
            msgs.append(n)
    msg = advisor.on_stop(ws, data, inp)
    if msg:
        msgs.append(msg)
    if msgs:
        _out({"systemMessage": "\n".join(msgs)})


NOTE_PREFIXES = ("ctx-note:", "ctx-note ")


def feedback_intercept(inp, data):
    """`ctx-note: <text>` saves a note; `ctx-notes` shows all; `ctx-notes clear` archives them.
    The prompt is blocked, so it never reaches Claude: zero tokens."""
    text = (inp.get("prompt") or "").strip()
    low = text.lower()
    ws = config.Workspace(_cwd(inp))
    if low.startswith(NOTE_PREFIXES):
        body = text.split(":", 1)[1] if ":" in text[:9] else text[9:]
        if not body.strip():
            return {"decision": "block", "reason": "ctx-kit: empty note. Usage: ctx-note: <what you observed>"}
        kind = "bug" if re.search(r"\b(bug|error|broke|fail|crash|wrong)\b", body, re.I) else "note"
        n = feedback.add_note(ws, data, body, kind)
        return {"decision": "block",
                "reason": "ctx-kit: note #%d saved on this machine (not sent to Claude). "
                          "Type ctx-notes to show all for a photo." % n}
    if low in ("ctx-notes", "ctx-notes show"):
        return {"decision": "block", "reason": feedback.photo_report(ws, data)}
    if low == "ctx-notes clear":
        feedback.clear_notes(data)
        return {"decision": "block", "reason": "ctx-kit: notes archived (a .bak copy is kept)."}
    return None


def prompt_submit(inp, data):
    fb = feedback_intercept(inp, data)
    if fb:
        _out(fb)
        return
    note = advisor.on_prompt(config.Workspace(_cwd(inp)), data, inp)
    if note:
        _out({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": note}})


HANDLERS = {
    "session-start": session_start,
    "session-end": session_end,
    "pre-compact": pre_compact,
    "post-read": post_read,
    "post-edit": post_edit,
    "stop": stop,
    "prompt": prompt_submit,
}


def main(event, data_dir):
    data = config.plugin_data_dir(data_dir)
    try:
        raw = sys.stdin.read()
        inp = json.loads(raw) if raw.strip() else {}
        if not isinstance(inp, dict):
            inp = {}
    except Exception:
        inp = {}
    try:
        h = HANDLERS.get(event)
        if h:
            h(inp, data)
    except Exception:
        util.log_error(data, "hook " + event)
    return 0

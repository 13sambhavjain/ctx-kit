"""read-guard: stop paying twice for file contents Claude already has.

PostToolUse(Read):
  - re-read of an unchanged range Claude already saw in this context -> one-line stub
  - whole-file re-read after the file changed (Claude's edits or outside changes) -> unified diff only
  - Claude Code's own "file unchanged" stub, but the file DID change outside Claude -> fresh content
  - repeating the exact same Read right after a stub/diff -> full text (escape hatch)
PostToolUse(Bash): observe-only stats for repeated read-like commands (nothing is changed).
PreCompact / SessionStart(compact|clear): forget what was seen (the context no longer has it).

Output replacement uses `updatedToolOutput` in the Read tool's own output shape. If Claude Code
rejects the shape, it silently keeps the original output, so the failure mode is "no savings".
Standard library only; Python 3.8+.
"""
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback

MAX_HASH_BYTES = 5_000_000
MAX_SNAPSHOT_BYTES = 400_000
MIN_STUB_CHARS = 300       # smaller outputs aren't worth replacing
MIN_DIFF_CHARS = 1500
DIFF_MAX_RATIO = 0.6       # use a diff only if it's well under the full text
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".ipynb", ".svgz"}
NATIVE_STUB = "unchanged since your last read"
READLIKE = re.compile(r"^\s*(?:cd\s+[^;&]+(?:&&|;)\s*)*(cat|head|tail|sed\s+-n|nl|less|bat)\b")


# ------------------------------------------------------------------ utils

def data_dir():
    d = os.environ.get("CLAUDE_PLUGIN_DATA")
    return os.path.join(d, "read-guard") if d else os.path.join(os.path.expanduser("~"), ".read-guard")


def sha(b):
    if isinstance(b, str):
        b = b.encode("utf-8", "replace")
    return hashlib.sha1(b).hexdigest()[:16]


def load_json(p, default):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".rg-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, p)


def safe(s):
    return re.sub(r"[^A-Za-z0-9_-]", "", s or "x")[:80] or "x"


def log_error(where):
    try:
        d = data_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "errors.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s: %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), where,
                                       traceback.format_exc(limit=3).strip().replace("\n", " | ")))
    except Exception:
        pass


def settings():
    return load_json(os.path.join(data_dir(), "settings.json"), {"enabled": True, "observe_bash": True})


def ctx_key(inp):
    return "%s-%s" % (safe(inp.get("session_id")), safe(inp.get("agent_id") or "main"))


def state_path(key):
    return os.path.join(data_dir(), "state", key + ".json")


def snap_path(key, path):
    return os.path.join(data_dir(), "snap", key, sha(os.path.normcase(path)) + ".txt")


def bump(stats_updates):
    p = os.path.join(data_dir(), "stats.json")
    st = load_json(p, {})
    for k, v in stats_updates.items():
        st[k] = st.get(k, 0) + v
    st.setdefault("since", time.strftime("%Y-%m-%d"))
    save_json(p, st)


def read_file(path):
    """(text or None, hash or None, size)."""
    try:
        size = os.path.getsize(path)
        if size > MAX_HASH_BYTES:
            st = os.stat(path)
            return None, "s%d-%d" % (st.st_size, int(st.st_mtime)), size
        with open(path, "rb") as f:
            raw = f.read()
        return raw.decode("utf-8", "replace"), sha(raw), size
    except OSError:
        return None, None, 0


# ------------------------------------------------------------------ ranges

def merge(ranges):
    out = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def covered(ranges, s, e):
    return any(a <= s and e <= b for a, b in ranges)


# ------------------------------------------------------------------ Read output shape

def read_output(path, content, total_lines):
    n = content.count("\n") + (0 if content.endswith("\n") else 1)
    return {"type": "text", "file": {"filePath": path, "content": content, "numLines": n,
                                       "startLine": 1, "totalLines": total_lines}}


def response_info(resp, tool_input, text):
    """Return (kind, start, end, total, chars). kind: text | native_stub | other."""
    if isinstance(resp, dict):
        f = resp.get("file") if isinstance(resp.get("file"), dict) else None
        rtype = str(resp.get("type") or "")
        blob = json.dumps(resp)[:2000].lower()
        if NATIVE_STUB in blob or "unchanged" in rtype:
            return "native_stub", None, None, None, 0
        if rtype == "text" and f is not None:
            start = int(f.get("startLine") or 1)
            num = int(f.get("numLines") or 0)
            total = int(f.get("totalLines") or num)
            return "text", start, start + max(num, 1) - 1, total, len(f.get("content") or "")
        return "other", None, None, None, 0
    if isinstance(resp, str):
        if NATIVE_STUB in resp.lower():
            return "native_stub", None, None, None, 0
        # unknown string shape: derive range from input
        lines = text.count("\n") + 1 if text else 0
        start = int(tool_input.get("offset") or 1)
        lim = tool_input.get("limit")
        end = start + int(lim) - 1 if lim else max(lines, start)
        return "text", start, min(end, max(lines, start)), lines, len(resp)
    return "other", None, None, None, 0


# ------------------------------------------------------------------ hooks

def on_read(inp):
    st_cfg = settings()
    if not st_cfg.get("enabled", True):
        return None
    ti = inp.get("tool_input") or {}
    fp = ti.get("file_path")
    if not fp:
        return None
    path = os.path.abspath(fp if os.path.isabs(fp) else os.path.join(inp.get("cwd") or os.getcwd(), fp))
    if os.path.splitext(path)[1].lower() in SKIP_EXT:
        return None
    text, h, size = read_file(path)
    if h is None:
        return None
    resp = inp.get("tool_response")
    kind, s, e, total, out_chars = response_info(resp, ti, text)
    key = ctx_key(inp)
    sp = state_path(key)
    state = load_json(sp, {})
    entry = state.get(path)
    lines_total = (text.count("\n") + (0 if text.endswith("\n") else 1)) if text else (total or 0)
    result = None
    stats = {"reads": 1}

    if kind == "native_stub":
        if entry and entry.get("hash") != h and text is not None:
            # Claude Code thinks it's unchanged, but the file changed outside Claude: give fresh content
            numbered = text if len(text) < 200_000 else text[:200_000]
            result = read_output(path, "[read-guard] Claude Code reported this file as unchanged, but it changed "
                                       "on disk since your last Read. Current content follows.\n" + numbered, lines_total)
            stats["stale_native_fixed"] = 1
            state[path] = {"hash": h, "ranges": [[1, lines_total]], "t": time.time()}
            _snapshot(key, path, text, whole=True)
        else:
            stats["native_stub"] = 1
    elif kind == "text":
        # "whole" = Claude saw every line (no offset/limit and not truncated by Claude Code)
        whole = not ti.get("offset") and not ti.get("limit") and e >= max(lines_total, 1)
        esc = state.get("_last_replaced")
        repeat = esc and esc.get("path") == path and esc.get("args") == [ti.get("offset"), ti.get("limit")]
        if repeat:
            stats["escape_full"] = 1
            state.pop("_last_replaced", None)
        elif entry and entry.get("hash") == h and covered(entry.get("ranges", []), s, e) and out_chars >= MIN_STUB_CHARS:
            a, b = s, e
            msg = ("[read-guard] Unchanged since your earlier Read of this file (lines %d-%d) in this conversation; "
                   "content omitted to save tokens. Use that earlier result. This is not an error. If that result "
                   "is no longer in your context, repeat this exact Read once to get the full text." % (a, b))
            result = read_output(path, msg, lines_total)
            stats["stubbed"] = 1
            stats["chars_saved"] = max(0, out_chars - len(msg))
            state["_last_replaced"] = {"path": path, "args": [ti.get("offset"), ti.get("limit")]}
        elif entry and entry.get("hash") != h and whole and entry.get("whole") and text is not None \
                and out_chars >= MIN_DIFF_CHARS:
            old = _load_snapshot(key, path)
            if old is not None:
                diff = "".join(difflib.unified_diff(old.splitlines(True), text.splitlines(True),
                                                    "before (your earlier Read)", "now", n=3))
                if diff and len(diff) < DIFF_MAX_RATIO * out_chars:
                    msg = ("[read-guard] This file changed since your earlier full Read in this conversation "
                           "(your edits and/or outside changes). Below is ONLY the change as a unified diff; hunk "
                           "headers give real line numbers, and everything else is exactly as you read it before. "
                           "This is not an error. If you need the full current text, repeat this exact Read once.\n\n"
                           + diff)
                    result = read_output(path, msg, lines_total)
                    stats["diffed"] = 1
                    stats["chars_saved"] = max(0, out_chars - len(msg))
                    state["_last_replaced"] = {"path": path, "args": [ti.get("offset"), ti.get("limit")]}
        # record what Claude now knows
        if result is None or stats.get("diffed"):
            if entry and entry.get("hash") == h:
                ranges = merge(entry.get("ranges", []) + [[s, e]])
            else:
                ranges = [[s, e]]
            known_whole = whole or (entry is not None and entry.get("hash") == h and entry.get("whole"))
            state[path] = {"hash": h, "ranges": ranges, "whole": bool(known_whole or stats.get("diffed")),
                           "t": time.time()}
            if whole and text is not None:
                _snapshot(key, path, text, whole=True)
    if kind != "other":
        save_json(sp, state)
    bump(stats)
    if result is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": result}}


def _snapshot(key, path, text, whole):
    if not whole or len(text) > MAX_SNAPSHOT_BYTES:
        return
    p = snap_path(key, path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _load_snapshot(key, path):
    try:
        with open(snap_path(key, path), "r", encoding="utf-8", newline="") as f:
            return f.read()
    except OSError:
        return None


def on_bash(inp):
    """Observe only: count repeated read-like commands with identical output."""
    if not settings().get("observe_bash", True):
        return None
    cmd = (inp.get("tool_input") or {}).get("command") or ""
    if not READLIKE.match(cmd):
        return None
    resp = inp.get("tool_response")
    out = resp.get("stdout", "") if isinstance(resp, dict) else (resp if isinstance(resp, str) else "")
    key = ctx_key(inp)
    p = os.path.join(data_dir(), "bash", key + ".json")
    seen = load_json(p, {})
    k = sha(cmd + "\0" + out)
    st = {"bash_readlike": 1, "bash_readlike_chars": len(out)}
    if k in seen:
        st["bash_repeat"] = 1
        st["bash_repeat_chars"] = len(out)
    seen[k] = 1
    save_json(p, seen)
    bump(st)
    return None


def forget(inp):
    """The context lost what was read (compaction, clear): drop state for this session."""
    sid = safe(inp.get("session_id"))
    for sub in ("state", "bash"):
        d = os.path.join(data_dir(), sub)
        if os.path.isdir(d):
            for name in os.listdir(d):
                if name.startswith(sid + "-"):
                    try:
                        os.remove(os.path.join(d, name))
                    except OSError:
                        pass
    snaps = os.path.join(data_dir(), "snap")
    if os.path.isdir(snaps):
        for name in os.listdir(snaps):
            if name.startswith(sid + "-"):
                shutil.rmtree(os.path.join(snaps, name), ignore_errors=True)


def prune(days=7):
    cutoff = time.time() - days * 86400
    for sub in ("state", "bash", "snap"):
        d = os.path.join(data_dir(), sub)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            p = os.path.join(d, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
            except OSError:
                pass


def on_session_start(inp):
    prune()
    if inp.get("source") in ("compact", "clear"):
        forget(inp)
    return None


HANDLERS = {"read": on_read, "bash": on_bash, "pre-compact": lambda i: forget(i), "session-start": on_session_start}


def hook_main(event):
    try:
        raw = sys.stdin.read()
        inp = json.loads(raw) if raw.strip() else {}
        if not isinstance(inp, dict):
            return 0
    except Exception:
        return 0
    try:
        out = HANDLERS.get(event, lambda i: None)(inp)
        if out:
            sys.stdout.write(json.dumps(out))
    except Exception:
        log_error("hook " + event)
    return 0


# ------------------------------------------------------------------ CLI

def cli(argv):
    cmd = argv[0] if argv else "stats"
    cfgp = os.path.join(data_dir(), "settings.json")
    cfg = settings()
    if cmd in ("on", "off"):
        cfg["enabled"] = cmd == "on"
        save_json(cfgp, cfg)
        print("read-guard %s" % cmd)
    elif cmd == "bash-observe":
        cfg["observe_bash"] = (argv[1:2] or ["on"])[0] == "on"
        save_json(cfgp, cfg)
        print("bash observation %s" % ("on" if cfg["observe_bash"] else "off"))
    elif cmd == "reset-stats":
        save_json(os.path.join(data_dir(), "stats.json"), {})
        print("stats reset")
    else:
        st = load_json(os.path.join(data_dir(), "stats.json"), {})
        tok = lambda c: "~%.1fK tokens" % (c / 3500.0)
        print("read-guard: %s (bash observation %s)" % ("on" if cfg.get("enabled", True) else "off",
                                                         "on" if cfg.get("observe_bash", True) else "off"))
        print("since %s" % st.get("since", "-"))
        print("  Read calls seen        : %d" % st.get("reads", 0))
        print("  replaced by stub       : %d" % st.get("stubbed", 0))
        print("  replaced by diff       : %d" % st.get("diffed", 0))
        print("  est. saved (attempted) : %s per occurrence, before re-sends" % tok(st.get("chars_saved", 0)))
        print("  escape (full re-read)  : %d" % st.get("escape_full", 0))
        print("  native 'unchanged' stub: %d   stale native stubs fixed: %d" % (st.get("native_stub", 0), st.get("stale_native_fixed", 0)))
        print("  Bash read-like cmds    : %d (%s); exact repeats: %d (%s)" % (
            st.get("bash_readlike", 0), tok(st.get("bash_readlike_chars", 0)),
            st.get("bash_repeat", 0), tok(st.get("bash_repeat_chars", 0))))
        print("Note: savings count only if Claude Code accepted the replacement. Saved tokens are also "
              "re-sent on every later request in the session, so real savings are larger.")
    return 0

"""ctxw: the only way ctx-kit writes context/handoff files on Claude's behalf.

Safety: every target must resolve inside the ctx root (or handoffs dir with
--handoffs). Symlinks on the way are refused, and names that look like Claude
Code configuration are refused even inside those folders.

Operations (content on stdin):
  write <rel> [--overwrite]   create a file (refuses to clobber unless --overwrite)
  append <rel>                append text
  replace <rel>               apply one or more blocks:
                                  <<<<<<< OLD
                                  exact old text
                                  =======
                                  new text
                                  >>>>>>> NEW
                              each OLD must occur exactly once, or nothing is written
  mv <rel> <rel2>             rename/move
  rm <rel>                    move to <root>/.trash/ (recoverable)
"""
import os
import re
import shutil
import sys

from . import redact, util

FORBIDDEN_NAME = re.compile(r"^(settings(\.local)?\.json|\.mcp\.json|hooks\.json|plugin\.json|marketplace\.json)$", re.I)
FORBIDDEN_DIRS = {"hooks", "skills", "agents", "commands", ".git"}


class WriteError(Exception):
    pass


def safe_path(root, relpath):
    if not relpath or relpath.strip() in (".", ""):
        raise WriteError("empty path")
    relpath = relpath.replace("\\", "/")
    if os.path.isabs(relpath) or re.match(r"^[A-Za-z]:", relpath):
        # allow absolute paths only if they are inside root
        target = os.path.abspath(relpath)
    else:
        target = os.path.abspath(os.path.join(root, relpath))
    root_abs = os.path.abspath(root)
    try:
        inside = os.path.commonpath([os.path.normcase(target), os.path.normcase(root_abs)]) == os.path.normcase(root_abs)
    except ValueError:  # different drives on Windows
        inside = False
    if not inside:
        raise WriteError("refusing path outside %s: %s" % (root_abs, relpath))
    parts = os.path.relpath(target, root_abs).replace(os.sep, "/").split("/")
    if any(p in FORBIDDEN_DIRS for p in parts[:-1]) or FORBIDDEN_NAME.match(parts[-1]):
        raise WriteError("refusing config-like path: %s" % relpath)
    # no symlinks anywhere between root and target
    cur = root_abs
    for p in parts:
        cur = os.path.join(cur, p)
        if os.path.islink(cur):
            raise WriteError("refusing symlinked path: %s" % relpath)
    return target


BLOCK = re.compile(r"<<<<<<< OLD\r?\n(.*?)\r?\n=======\r?\n(.*?)\r?\n?>>>>>>> NEW", re.S)


def parse_blocks(spec):
    blocks = BLOCK.findall(spec)
    if not blocks:
        raise WriteError("no replace blocks found (expected <<<<<<< OLD / ======= / >>>>>>> NEW)")
    return blocks


def apply_replace(text, spec):
    """Apply blocks; only NEW text is redacted (OLD must match the file as-is)."""
    blocks = parse_blocks(spec)
    kinds = []
    for old, new in blocks:
        new, k = redact.redact(new)
        kinds += k
        n = text.count(old)
        if n != 1:
            # tolerate CRLF files
            if "\r\n" in text and text.count(old.replace("\n", "\r\n")) == 1:
                old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
            else:
                raise WriteError("OLD text found %d times (must be exactly 1); re-read the file and retry:\n%s"
                                 % (n, old[:200]))
        text = text.replace(old, new, 1)
    return text, len(blocks), sorted(set(kinds))


def run(root, op, args, stdin_text, overwrite=False):
    """Execute one op. Returns a short human message. Raises WriteError."""
    if op in ("write", "append", "replace"):
        if len(args) != 1:
            raise WriteError("%s needs exactly one path" % op)
        target = safe_path(root, args[0])
        if op == "replace":
            content, kinds = stdin_text or "", []
        else:
            content, kinds = redact.redact(stdin_text or "")
        note = (" (redacted: %s)" % ", ".join(kinds)) if kinds else ""
        if op == "write":
            if os.path.exists(target) and not overwrite:
                raise WriteError("exists: %s (use replace, or write --overwrite)" % args[0])
            util.atomic_write(target, content if content.endswith("\n") else content + "\n")
            return "wrote %s%s" % (args[0], note)
        if op == "append":
            old = util.read_text(target) if os.path.exists(target) else ""
            sep = "" if (not old or old.endswith("\n")) else "\n"
            util.atomic_write(target, old + sep + content + ("" if content.endswith("\n") else "\n"))
            return "appended to %s%s" % (args[0], note)
        if not os.path.exists(target):
            raise WriteError("no such file: %s" % args[0])
        new_text, n, kinds = apply_replace(util.read_text(target), content)
        util.atomic_write(target, new_text)
        note = (" (redacted: %s)" % ", ".join(kinds)) if kinds else ""
        return "replaced %d block(s) in %s%s" % (n, args[0], note)
    if op == "mv":
        if len(args) != 2:
            raise WriteError("mv needs two paths")
        a, b = safe_path(root, args[0]), safe_path(root, args[1])
        if not os.path.exists(a):
            raise WriteError("no such file: %s" % args[0])
        if os.path.exists(b):
            raise WriteError("target exists: %s" % args[1])
        os.makedirs(os.path.dirname(b), exist_ok=True)
        shutil.move(a, b)
        return "moved %s -> %s" % (args[0], args[1])
    if op == "rm":
        if len(args) != 1:
            raise WriteError("rm needs one path")
        a = safe_path(root, args[0])
        if not os.path.exists(a):
            raise WriteError("no such file: %s" % args[0])
        trash = os.path.join(root, ".trash", util.now_iso().replace(":", ""))
        os.makedirs(trash, exist_ok=True)
        shutil.move(a, os.path.join(trash, os.path.basename(a)))
        return "moved %s to .trash/" % args[0]
    raise WriteError("unknown op: %s" % op)


def read_stdin():
    try:
        data = sys.stdin.buffer.read()
    except Exception:
        return ""
    return data.decode("utf-8", "replace")

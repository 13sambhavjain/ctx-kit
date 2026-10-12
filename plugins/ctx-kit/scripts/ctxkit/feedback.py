"""/ctx-feedback: build a redacted, diagnostics-only GitHub issue draft.

Never includes code, file contents, project names or paths. The user reviews the
draft and opens the prefilled link themselves; nothing is submitted from here.
"""
import json
import os
import platform
import re
import sys
import textwrap
from urllib.parse import quote

from . import check, config, redact, tree, util

MAX_URL = 7000


def _plugin_meta():
    p = os.path.join(config.plugin_root(), ".claude-plugin", "plugin.json")
    return util.load_json(p, {}) or {}


def _scrub_paths(text, ws):
    """Collapse whole paths (not just their prefix) so no folder, project or file name survives."""
    text = re.sub(r"(?i)\b[A-Z]:[\\/][^\s|\"')]+", "<path>", text)                 # Windows drive paths
    text = re.sub(r"\\\\[^\s|\"')]+", "<path>", text)                                 # UNC paths
    for p in (ws.root, os.path.expanduser("~")):
        if p:
            for form in (p, p.replace("\\", "/")):
                text = re.sub(re.escape(form) + r"[^\s|\"')]*", "<path>", text)
    text = re.sub(r"(?<![\w.])/(?:Users|home|private|var|tmp|opt|mnt|srv|workspace|data)/[^\s|\"')]+", "<path>", text)
    return text


def diagnostics(ws, data_dir, full=True):
    meta = _plugin_meta()
    d = {
        "plugin_version": meta.get("version", "?"),
        "os": "%s %s" % (platform.system(), platform.release()),
        "python": "%s (%s)" % (platform.python_version(), os.path.basename(sys.executable)),
        "shell": os.path.basename(os.environ.get("SHELL") or os.environ.get("ComSpec") or "?"),
        "git": "yes" if config.has_git() else "no",
        "initialized": ws.initialized,
    }
    if ws.initialized:
        for k in ("storage", "local_only", "nested_git", "write_mode", "review", "rules"):
            d[k] = ws.get(k)
        nodes = tree.list_nodes(ws.ctx_root)
        d["nodes"] = len(nodes)
        try:
            if not full:
                raise StopIteration
            _, counts = check.run_check(ws, fix=False)
            d["check"] = ", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "clean"
        except StopIteration:
            d["check"] = "(run /ctx check)"
        except Exception as e:  # diagnostics must never fail
            d["check"] = "error: %s" % type(e).__name__
    errs = ""
    p = os.path.join(config.plugin_data_dir(data_dir), "errors.log")
    if os.path.exists(p):
        errs = "\n".join(util.read_text(p).splitlines()[-15:])
    return d, _scrub_paths(errs, ws)


def build(ws, data_dir, note, kind="observation"):
    d, errs = diagnostics(ws, data_dir)
    title = "[%s] %s" % (kind, (note.strip().splitlines() or ["ctx-kit feedback"])[0][:80])
    body = ["### What happened / suggestion", "", note.strip() or "_(none)_", "",
            "### Diagnostics (auto, no code or paths)", "", "| key | value |", "|---|---|"]
    body += ["| %s | %s |" % (k, v) for k, v in d.items()]
    if errs:
        body += ["", "### Recent hook errors", "", "```", errs, "```"]
    text = _scrub_paths("\n".join(body), ws)
    text, kinds = redact.redact(text)
    repo = (_plugin_meta().get("repository") or "").rstrip("/")
    url = None
    if repo.startswith("https://github.com/"):
        url = "%s/issues/new?title=%s&body=%s&labels=%s" % (repo, quote(title), quote(text), quote(kind))
        if len(url) > MAX_URL:
            url = None
    return title, text, url, kinds


# ------------------------------------------------------------------ local notes (no network, no tokens)



def _notes_path(data_dir):
    return os.path.join(config.plugin_data_dir(data_dir), "feedback", "notes.jsonl")


def add_note(ws, data_dir, text, kind="note"):
    """Save one observation locally (redacted, paths scrubbed). Returns its number."""
    p = _notes_path(data_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    n = 1
    if os.path.exists(p):
        n = len([l for l in util.read_text(p).splitlines() if l.strip()]) + 1
    clean, _ = redact.redact(_scrub_paths(text.strip(), ws))
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"n": n, "t": util.now_iso()[:16], "kind": kind, "text": clean}) + "\n")
    return n


def notes(data_dir):
    p = _notes_path(data_dir)
    out = []
    if os.path.exists(p):
        for l in util.read_text(p).splitlines():
            try:
                out.append(json.loads(l))
            except ValueError:
                pass
    return out


def clear_notes(data_dir):
    p = _notes_path(data_dir)
    if os.path.exists(p):
        os.replace(p, p + ".%s.bak" % util.now_iso().replace(":", ""))


def photo_report(ws, data_dir, width=88, full=False):
    """One compact screen meant to be photographed: diagnostics + numbered notes."""
    d, errs = diagnostics(ws, data_dir, full=full)
    keys = ["plugin_version", "os", "python", "shell", "git", "initialized", "storage", "local_only",
            "nested_git", "write_mode", "nodes", "check"]
    head = " | ".join("%s=%s" % (k.replace("plugin_version", "v"), d[k]) for k in keys if k in d)
    out = ["=== ctx-kit feedback (%s) ===" % util.now_iso()[:16]]
    out += textwrap.wrap(head, width)
    if errs:
        last = errs.splitlines()[-3:]
        out.append("hook errors (last %d):" % len(last))
        out += ["  " + e[:width - 2] for e in last]
    ns = notes(data_dir)
    out.append("notes: %d" % len(ns))
    for x in ns:
        for chunk in textwrap.wrap("%d. [%s %s] %s" % (x["n"], x["kind"], x["t"][5:], x["text"]), width,
                              subsequent_indent="   "):
            out.append(chunk)
    out.append("=== end ===")
    return "\n".join(out)

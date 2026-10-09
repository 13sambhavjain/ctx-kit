"""/ctx-feedback: build a redacted, diagnostics-only GitHub issue draft.

Never includes code, file contents, project names or paths. The user reviews the
draft and opens the prefilled link themselves; nothing is submitted from here.
"""
import os
import platform
import re
import sys
from urllib.parse import quote

from . import check, config, redact, tree, util

MAX_URL = 7000


def _plugin_meta():
    p = os.path.join(config.plugin_root(), ".claude-plugin", "plugin.json")
    return util.load_json(p, {}) or {}


def _scrub_paths(text, ws):
    home = os.path.expanduser("~")
    for p, label in ((ws.root, "<workspace>"), (home, "<home>")):
        if p:
            text = text.replace(p, label).replace(p.replace("\\", "/"), label)
    return re.sub(r"(?i)\b[A-Z]:[\\/][^\s|]+", "<path>", text)


def diagnostics(ws, data_dir):
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
            _, counts = check.run_check(ws, fix=False)
            d["check"] = ", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "clean"
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

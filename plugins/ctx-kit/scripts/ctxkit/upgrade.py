"""Tell the user (and the agent) what an existing setup should change after a plugin update.

`ctx status` shows these hints (and /ctx injects status), SessionStart shows one line to the user once
per new version, and `/ctx upgrade` walks through them. Hints are computed from the workspace itself,
so they're correct whatever version the tree was created with.
"""
import os

from . import __version__, autocommit, tree, util


def hints(ws):
    """[(id, why, command)] of recommended changes for this workspace."""
    out = []
    if ws.initialized:
        if not ws.get("nested_git") and (ws.get("local_only") or ws.get("storage") == "home"):
            out.append(("history", "The tree has no history: auto-commits and protected-line checks need it.",
                        "ctx config set nested_git true   then   /ctx init (keeps existing nodes)"))
        legacy = []
        for r in tree.list_nodes(ws.ctx_root):
            try:
                meta, _, _ = tree.load_node(ws.ctx_root, r)
            except OSError:
                continue
            if meta.get("status") == "current" and not meta.get("body_hash"):
                legacy.append(r)
        if legacy:
            out.append(("legacy", "%d node(s) are marked current but were never verified with a v0.3 stamp "
                        "(e.g. migrated by hand), so they may look verified when they aren't." % len(legacy),
                        "ctx mark-legacy-draft   then audit them gradually with /ctx audit"))
        p = tree.snippet_target(ws)
        text = util.read_text(p) if os.path.exists(p) else ""
        if tree.SNIP_START in text and "/ctx search" not in text:
            out.append(("snippet", "The CLAUDE pointer block is from an older version (no search hint).",
                        "ctx index   (refreshes only the marked ctx-kit block)"))
        if not ws.get("protected_tags"):
            out.append(("tags", "Optional: protect your own lines (e.g. [SJ]) from being removed by agents.",
                        "ctx config set protected_tags '[\"[SJ]\"]'"))
    if ws.configured and "handoffs_local_only" in (ws.local or {}) and ws.get("handoffs_local_only") \
            and not os.path.isdir(os.path.join(ws.handoffs_dir, ".git")):
        out.append(("handoff-history", "Handoffs have no history.",
                    "ctx handoff setup --local-only y --history y"))
    return out


def needs_notice(ws):
    return ws.configured and (ws.local or {}).get("plugin_version_seen") != __version__


def render(ws):
    hs = hints(ws)
    if not hs:
        return ""
    seen = (ws.local or {}).get("plugin_version_seen") or "an older version"
    lines = ["UPGRADE NOTES (plugin %s; this project was set up with %s). Ask the user before applying:" % (__version__, seen)]
    for i, (hid, why, cmd) in enumerate(hs, 1):
        lines.append("  %d. [%s] %s\n     -> %s" % (i, hid, why, cmd))
    lines.append("  When done (or to dismiss): ctx upgrade --done")
    return "\n".join(lines)


def mark_done(ws):
    ws.save({"plugin_version_seen": __version__})

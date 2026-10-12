"""Automatic history for the tree and handoffs (nested git repos), with zero-token commit messages.

Runs at the end of each turn (Stop hook), and before compaction / at session end, so nothing
depends on the model remembering to commit. Messages are generated from the staged diff: which
nodes changed, how much, which sections. A file where a protected-tag line (e.g. "[SJ]") was
removed (not merely moved to another node) is held back and reported once, with the way to fix it.
Failures (git errors, a stale lock) are reported instead of silently disabling history.
"""
import os
import re
import time

from . import config, util

HANDOFF_IGNORE = ".work/\n.checkpoints/\n"
TREE_IGNORE = ".trash/\n*.swp\n*~\n.DS_Store\n.obsidian/\n"
LOCK_STALE_SECONDS = 120


def roots(ws):
    """(label, path, ignore) for each managed folder with history enabled and a nested repo."""
    out = []
    if ws.initialized and ws.get("nested_git") and os.path.isdir(os.path.join(ws.ctx_root, ".git")):
        out.append(("ctx", ws.ctx_root, TREE_IGNORE))
    if os.path.isdir(os.path.join(ws.handoffs_dir, ".git")) and ws.get("handoffs_history", True):
        out.append(("handoffs", ws.handoffs_dir, HANDOFF_IGNORE))
    return out


def init_repo(path, ignore=None):
    if not config.has_git():
        return "skipped (git not installed)"
    os.makedirs(path, exist_ok=True)
    if not os.path.isdir(os.path.join(path, ".git")):
        if config.git(["init", "-q"], path) is None:
            return "git init failed"
    if ignore and not os.path.exists(os.path.join(path, ".gitignore")):
        util.atomic_write(os.path.join(path, ".gitignore"), ignore)
    return "ok"


def _clear_stale_lock(root):
    lock = os.path.join(root, ".git", "index.lock")
    try:
        if os.path.exists(lock) and time.time() - os.path.getmtime(lock) > LOCK_STALE_SECONDS:
            os.remove(lock)
            return True
    except OSError:
        pass
    return False


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def parse_staged(root):
    """One `git diff --cached -U0` for everything -> {path: {"added": [...], "removed": [...], "hunks": [new_start...]}}."""
    diff = config.git(["diff", "--cached", "-U0", "--no-color"], root, raw=True) or ""
    files, cur = {}, None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            cur = None
        elif line.startswith("+++ "):
            p = line[4:]
            if p.startswith("b/"):
                p = p[2:]
            cur = files.setdefault(p, {"added": [], "removed": [], "hunks": []}) if p != "/dev/null" else None
        elif line.startswith("--- "):
            p = line[4:]
            if p.startswith("a/"):
                p = p[2:]
            # deleted files: attribute removals to the old path
            if p != "/dev/null":
                cur = files.setdefault(p, {"added": [], "removed": [], "hunks": []})
        elif cur is not None and line.startswith("@@"):
            m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", line)
            if m:
                cur["hunks"].append(int(m.group(1)))
        elif cur is not None and line.startswith("+"):
            cur["added"].append(line[1:])
        elif cur is not None and line.startswith("-"):
            cur["removed"].append(line[1:])
    return files


def _tree_lines(root):
    """Normalised lines of every markdown file currently in the folder (to recognise moved lines)."""
    seen = set()
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in (".git", ".trash")]
        for f in fns:
            if f.endswith(".md"):
                try:
                    seen.update(_norm(l) for l in util.read_text(os.path.join(dp, f)).splitlines())
                except OSError:
                    pass
    return seen


def protected_removals(staged, tags, root):
    """{path: [removed tagged lines]} for tagged lines that no longer exist anywhere in the folder."""
    if not tags:
        return {}
    everywhere = None
    out = {}
    for path, d in staged.items():
        cand = [l for l in d["removed"] if any(t in l for t in tags)]
        if not cand:
            continue
        if everywhere is None:
            everywhere = _tree_lines(root)
        gone = [l.strip() for l in cand if _norm(l) not in everywhere]
        if gone:
            out[path] = gone
    return out


def _headings(text):
    return [(i, m.group(2).strip()) for i, line in enumerate(text.splitlines(), 1)
            for m in [re.match(r"^(#{1,4})\s+(.+?)\s*#*\s*$", line)] if m]


def _sections(root, path, hunks):
    p = os.path.join(root, path)
    if not hunks or not path.endswith(".md") or not os.path.exists(p):
        return []
    hs = _headings(util.read_text(p))
    names = []
    for ln in hunks:
        above = [h for i, h in hs if i <= ln]
        name = above[-1] if above else "(frontmatter/top)"
        if name not in names:
            names.append(name)
    return names


def build_message(root, label, session_id, staged):
    ns = config.git(["diff", "--cached", "--name-status", "-M"], root) or ""
    stat = {}
    for line in (config.git(["diff", "--cached", "--numstat", "-M"], root) or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            stat[parts[-1]] = (parts[0], parts[1])
    entries = [(parts[0][0], parts[-1]) for parts in (l.split("\t") for l in ns.splitlines()) if len(parts) >= 2]
    if not entries:
        return None
    verbs = {"A": "add", "M": "update", "D": "remove", "R": "rename"}
    kinds = set(c for c, _ in entries)
    verb = verbs.get(next(iter(kinds)), "update") if len(kinds) == 1 else "update"
    names = [os.path.splitext(os.path.basename(p))[0] for _, p in entries]
    subject = "%s: %s %s" % (label, verb, ", ".join(names[:3]) + (" (+%d more)" % (len(names) - 3) if len(names) > 3 else ""))
    body = []
    for code, path in entries:
        add, rem = stat.get(path, ("?", "?"))
        line = "%s %s (+%s -%s)" % (code, path, add, rem)
        if code == "M":
            secs = _sections(root, path, (staged.get(path) or {}).get("hunks", []))
            if secs:
                line += " | sections: " + "; ".join(secs[:4]) + (" …" if len(secs) > 4 else "")
        body.append(line)
    body += ["", "session %s | auto-commit by ctx-kit (no model tokens)" % ((session_id or "?")[:8])]
    return subject[:72] + "\n\n" + "\n".join(body)


def _once(data, key):
    """True the first time a key is seen (per plugin data dir); used to avoid repeating warnings."""
    if not data:
        return True
    p = os.path.join(data, "warned.json")
    seen = util.load_json(p, {}) or {}
    if key in seen:
        return False
    seen[key] = time.time()
    if len(seen) > 500:
        seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-300:])
    util.save_json(p, seen)
    return True


def commit_root(ws, label, root, session_id, message=None, data=None, allow_protected=False, ignore=None):
    """Stage, hold back protected removals, commit. Returns (subject or None, [warnings])."""
    warnings = []
    if ignore and not os.path.exists(os.path.join(root, ".gitignore")):
        util.atomic_write(os.path.join(root, ".gitignore"), ignore)
    if _clear_stale_lock(root):
        warnings.append("%s history: removed a stale git lock left by an interrupted commit" % label)
    if config.git(["add", "-A"], root) is None:
        if _once(data, "addfail:%s:%s" % (root, session_id)):
            warnings.append("%s history: `git add` failed in %s, so changes are NOT being recorded. Check `git -C \"%s\" status`."
                            % (label, root, root))
        return None, warnings
    staged = parse_staged(root)
    if not staged:
        return None, warnings
    if not allow_protected:
        for path, gone in protected_removals(staged, ws.get("protected_tags") or [], root).items():
            config.git(["reset", "-q", "--", path], root)
            staged.pop(path, None)
            key = "prot:%s:%s:%s" % (root, path, util.sha("\n".join(gone)))
            if _once(data, key):
                warnings.append("%s/%s: protected line(s) removed, so this file is held back from history until "
                                "resolved: %s. Restore the line, or approve with `ctx commit --allow-protected`."
                                % (label, path, " | ".join(g[:70] for g in gone[:2])))
    if not staged:
        return None, warnings
    msg = message or build_message(root, label, session_id, staged)
    if not msg:
        return None, warnings
    out = config.git(["-c", "user.name=ctx-kit", "-c", "user.email=ctx-kit@localhost", "commit", "-q", "-m", msg], root, timeout=20)
    if out is None:
        if _once(data, "commitfail:%s:%s" % (root, session_id)):
            warnings.append("%s history: commit failed, so changes are NOT being recorded. Check `git -C \"%s\" status`."
                            % (label, root))
        return None, warnings
    return msg.splitlines()[0], warnings


def run(ws, session_id, message=None, data=None, allow_protected=False):
    """Commit all managed roots. Returns (list of subjects, list of warnings)."""
    if not ws.get("auto_commit", True) and message is None:
        return [], []
    subjects, warnings = [], []
    for label, root, ignore in roots(ws):
        s, w = commit_root(ws, label, root, session_id, message, data, allow_protected, ignore)
        if s:
            subjects.append(s)
        warnings += w
    return subjects, warnings

"""The context tree: nodes, INDEX generation, init skeleton, rules, CLAUDE.md snippet."""
import os
import re

from . import config, sources, util

INDEX = "INDEX.md"
GEN_START = "<!-- ctx-kit:generated:start (do not edit by hand; run `ctx index`) -->"
GEN_END = "<!-- ctx-kit:generated:end -->"
SNIP_START = "<!-- ctx-kit:start -->"
SNIP_END = "<!-- ctx-kit:end -->"
RULE_PREFIX = "ctx-"
SKIP = {".trash", ".git"}


# ------------------------------------------------------------------ nodes

def list_nodes(ctx_root):
    """All .md files in the tree except INDEX.md, sorted, as relative paths."""
    out = []
    if not os.path.isdir(ctx_root):
        return out
    for dp, dns, fns in os.walk(ctx_root):
        dns[:] = sorted(d for d in dns if d not in SKIP)
        for f in sorted(fns):
            if f.endswith(".md"):
                r = util.rel(os.path.join(dp, f), ctx_root)
                if r != INDEX:
                    out.append(r)
    return out


def load_node(ctx_root, relpath):
    text = util.read_text(os.path.join(ctx_root, relpath))
    meta, body = util.parse_frontmatter(text)
    return meta, body, text


def title_of(meta, body, relpath):
    if meta.get("title"):
        return meta["title"]
    m = re.search(r"^#\s+(.+)$", body, re.M)
    if m:
        return m.group(1).strip()
    return os.path.splitext(os.path.basename(relpath))[0].replace("-", " ").title()


def as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [x.strip() for x in str(v).split(",") if x.strip()]


# ------------------------------------------------------------------ INDEX

def render_index_block(ws):
    root = ws.ctx_root
    rows, routes, decisions = [], [], []
    for r in list_nodes(root):
        try:
            meta, body, _ = load_node(root, r)
        except OSError:
            continue
        t = title_of(meta, body, r)
        summ = meta.get("summary") or ""
        flag = " ⚠ stale-suspect" if meta.get("status") == "stale-suspect" else ""
        if r.startswith("decisions/") and r != "decisions/DECISIONS.md":
            decisions.append((r, t, meta.get("decision") or meta.get("status") or ""))
            continue
        rows.append((r, "- [%s](%s) — %s%s" % (t, r, summ, flag)))
        for p in as_list(meta.get("paths")):
            routes.append("| `%s` | [%s](%s) |" % (p, t, r))
    out = [GEN_START, "", "## Nodes", ""] + (_group_rows(rows) or ["_(no nodes yet)_"])
    out += ["", "## Routing: working on these paths? read the node first", ""]
    if routes:
        out += ["| Code path | Read |", "|---|---|"] + routes
    else:
        out += ["_(no `paths:` set on any node yet)_"]
    if decisions:
        out += ["", "## Decisions", ""] + ["- [%s](%s) — %s" % (t, r, s) for r, t, s in decisions]
    out += ["", GEN_END]
    return "\n".join(out)


def _group_rows(rows):
    """Top-level nodes first, then one bullet per folder with its nodes nested."""
    top = [line for r, line in rows if "/" not in r]
    folders = {}
    for r, line in rows:
        if "/" in r:
            folders.setdefault(r.rsplit("/", 1)[0], []).append(line)
    out = list(top)
    for f in sorted(folders):
        out.append("- **%s/**" % f)
        out += ["  " + line for line in folders[f]]
    return out


def write_index(ws):
    p = os.path.join(ws.ctx_root, INDEX)
    block = render_index_block(ws)
    if os.path.exists(p):
        text = util.read_text(p)
        if GEN_START in text and GEN_END in text:
            a = text.index(GEN_START)
            b = text.index(GEN_END) + len(GEN_END)
            new = text[:a] + block + text[b:]
        else:
            new = text.rstrip("\n") + "\n\n" + block + "\n"
    else:
        new = INDEX_HEAD + "\n" + block + "\n"
    if not os.path.exists(p) or util.read_text(p) != new:
        util.atomic_write(p, new)
    return p


INDEX_HEAD = """# Context index

The root of this project's context tree. Read this first, then open **only** the
branches relevant to the task. Each node holds one topic; facts live in exactly one
node and other nodes link to it. Decisions live in `decisions/` (never edited once
accepted; a newer decision supersedes an older one).

## Overview

_One paragraph: what this project is, the main parts, and how they fit together._
"""


# ------------------------------------------------------------------ rules (.claude/rules)

def rules_dir(ws):
    return os.path.join(ws.root, ".claude", "rules")


def write_rules(ws):
    """One rule file per node with paths:, so the pointer loads lazily."""
    if not ws.get("rules", True):
        return []
    d = rules_dir(ws)
    wanted = {}
    for r in list_nodes(ws.ctx_root):
        try:
            meta, body, _ = load_node(ws.ctx_root, r)
        except OSError:
            continue
        paths = as_list(meta.get("paths"))
        if not paths:
            continue
        slug = RULE_PREFIX + re.sub(r"[^a-z0-9]+", "-", os.path.splitext(r)[0].lower()).strip("-")
        link = node_link_from_root(ws, r)
        content = ("---\npaths: [%s]\n---\n\nctx-kit: before changing code here, read the context node "
                   "`%s` (%s). If you learn something durable, update that node (see /ctx update).\n"
                   % (", ".join('"%s"' % p for p in paths), link, meta.get("summary") or title_of(meta, body, r)))
        wanted[slug + ".md"] = content
    written = []
    if wanted:
        os.makedirs(d, exist_ok=True)
    for name, content in wanted.items():
        p = os.path.join(d, name)
        if not os.path.exists(p) or util.read_text(p) != content:
            util.atomic_write(p, content)
            written.append(name)
    # remove stale generated rules
    if os.path.isdir(d):
        for name in os.listdir(d):
            if name.startswith(RULE_PREFIX) and name.endswith(".md") and name not in wanted:
                os.remove(os.path.join(d, name))
    return written


def node_link_from_root(ws, relnode):
    p = os.path.join(ws.ctx_root, relnode)
    r = util.rel(p, ws.root)
    if r.startswith(".."):
        return p.replace(os.sep, "/")  # home storage: absolute path
    return r


# ------------------------------------------------------------------ init

STUBS = [
    ("architecture.md", "Architecture", "Main components, their responsibilities and how they connect."),
    ("code-flow.md", "Code flow", "How a request/run flows through the code, step by step, with entry points."),
    ("concepts.md", "Concepts and conventions", "Domain vocabulary, design style and coding conventions, and why."),
    ("references.md", "References", "Specs, standards, external docs and facts the code relies on (with links)."),
    ("progress.md", "Progress", "What is done, in progress and next, at project level (not per session)."),
    ("api/README.md", "API reference", "Public interfaces: entry points, functions/classes, protocols and messages."),
]

NODE_BODY = """# {title}

_Fill in. Keep one topic per node; link to other nodes instead of repeating them.
Record where each fact comes from in `sources:` (path or path#Symbol)._
"""

DECISIONS_MD = """---
summary: Index of decisions (ADR style). Never edit an accepted decision; supersede it.
---

# Decisions

| ID | Title | Date | Who | Status |
|---|---|---|---|---|
| [D-0001](0001-context-storage.md) | Where and how this context tree is stored | {date} | user | accepted |
"""

DECISION_0001 = """---
title: "D-0001: Context storage"
summary: Where this context tree lives, whether it is local-only, and why.
decision: accepted
---

# D-0001: Context storage

- **Date:** {date}
- **Who:** user (via /ctx init)
- **Decision:** storage = `{storage}`, local-only = `{local_only}`, nested git = `{nested_git}`, write mode = `{write_mode}`.
- **Why:** _(fill in the reason given at init)_
- **Alternatives:** workspace `.claude/ctx`, `.ctx/`, or home `~/.ctx-kit/trees/<repo-id>`; committed vs local-only.
- **Affects:** [INDEX](../INDEX.md)
"""

TOP_SKIP = sources.SKIP_DIRS | {"docs", "test", "tests", "spec", "examples", "assets", "public", "static", "scripts"}


def top_level_code_dirs(root, limit=12):
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for n in names:
        p = os.path.join(root, n)
        if not os.path.isdir(p) or n.startswith(".") or n in TOP_SKIP:
            continue
        # does it contain code at all?
        has = False
        for _ in sources.iter_code_files(p):
            has = True
            break
        if has:
            out.append(n)
        if len(out) >= limit:
            break
    return out


def init(ws, storage, local_only, nested_git, write_mode, review, rules=True, overview=None):
    """Create config + skeleton. Idempotent: never overwrites existing nodes."""
    report = []
    ws.save({"storage": storage, "local_only": bool(local_only), "nested_git": bool(nested_git),
             "write_mode": write_mode, "review": review, "rules": bool(rules)})
    report.append("config: %s" % util.rel(ws.config_path, ws.root))
    root = ws.ctx_root
    os.makedirs(root, exist_ok=True)
    date = util.today()

    def stub(relp, content):
        p = os.path.join(root, relp)
        if not os.path.exists(p):
            util.atomic_write(p, content)
            report.append("created %s" % relp)

    for relp, title, summary in STUBS:
        stub(relp, util.dump_frontmatter({"summary": summary, "parent": "INDEX.md", "status": "draft"},
                                         NODE_BODY.format(title=title)))
    for d in top_level_code_dirs(ws.root):
        stub("modules/%s.md" % d, util.dump_frontmatter(
            {"summary": "The `%s/` area: purpose, key files, how it is used." % d, "parent": "INDEX.md",
             "paths": ["%s/**" % d], "sources": ["%s/" % d], "status": "draft"},
            NODE_BODY.format(title="Module: %s" % d)))
    stub("decisions/DECISIONS.md", DECISIONS_MD.format(date=date))
    stub("decisions/0001-context-storage.md", DECISION_0001.format(
        date=date, storage=storage, local_only=local_only, nested_git=nested_git, write_mode=write_mode))
    if not os.path.exists(os.path.join(root, INDEX)):
        head = INDEX_HEAD
        if overview:
            head = head.replace("_One paragraph: what this project is, the main parts, and how they fit together._", overview.strip())
        util.atomic_write(os.path.join(root, INDEX), head + "\n" + render_index_block(ws) + "\n")
        report.append("created INDEX.md")
    write_index(ws)
    rules_written = write_rules(ws)
    if rules_written:
        report.append("rules: %d file(s) in .claude/rules/" % len(rules_written))
    report.append(write_snippet(ws))
    if local_only:
        report.append(add_excludes(ws))
    if nested_git:
        report.append(init_nested_git(ws))
    n = ws.worktree_count()
    if n > 1 and storage != "home":
        report.append("WARNING: this repo has %d worktrees. With storage=%s each worktree gets its own "
                      "separate tree (local-only files are not shared). Use storage=home to share one tree." % (n, storage))
    return report


SNIPPET = """{start}
## Project context (ctx-kit)
This project keeps a context tree at `{index}`. Before non-trivial work, read that
INDEX and then only the nodes relevant to the task (follow its routing table).
Facts live in one node each; decisions live in `decisions/`. Nodes are plain links,
never imports, so they cost nothing until opened. When you learn something durable
(a fact, a flow, a decision and its reason), record it with `/ctx update`.
{end}
"""


def snippet_target(ws):
    name = "CLAUDE.local.md" if ws.get("local_only") else "CLAUDE.md"
    return os.path.join(ws.root, name)


def write_snippet(ws):
    p = snippet_target(ws)
    index_link = node_link_from_root(ws, INDEX)
    snip = SNIPPET.format(start=SNIP_START, end=SNIP_END, index=index_link)
    text = util.read_text(p) if os.path.exists(p) else ""
    if SNIP_START in text and SNIP_END in text:
        a = text.index(SNIP_START)
        b = text.index(SNIP_END) + len(SNIP_END)
        new = text[:a] + snip.rstrip("\n") + text[b:]
    else:
        new = (text.rstrip("\n") + "\n\n" if text.strip() else "") + snip
    if new != text:
        util.atomic_write(p, new)
        return "snippet: %s" % os.path.basename(p)
    return "snippet: %s (unchanged)" % os.path.basename(p)


def add_excludes(ws):
    excl = ws.git_dir_info()
    if not excl:
        return "excludes: skipped (not a git repo or git not installed)"
    entries = ["/" + util.rel(ws.config_path, ws.root), "/.claude/rules/%s*.md" % RULE_PREFIX, "/CLAUDE.local.md"]
    if ws.get("storage") != "home":
        entries.insert(0, "/" + util.rel(ws.ctx_root, ws.root) + "/")
    existing = util.read_text(excl) if os.path.exists(excl) else ""
    have = set(l.strip() for l in existing.splitlines())
    add = [e for e in entries if e not in have]
    if add:
        os.makedirs(os.path.dirname(excl), exist_ok=True)
        block = ("" if existing.endswith("\n") or not existing else "\n") + "# ctx-kit (local-only context)\n" + "\n".join(add) + "\n"
        with open(excl, "a", encoding="utf-8") as f:
            f.write(block)
    return "excludes: %s" % (", ".join(add) if add else "already present")


def init_nested_git(ws):
    if not config.has_git():
        ws.save({"nested_git": False})
        return "nested git: skipped (git not installed); recorded nested_git=false"
    root = ws.ctx_root
    if not ws.get("local_only") and ws.get("storage") != "home":
        ws.save({"nested_git": False})
        return "nested git: skipped (tree is committed with the project, so the project's git already versions it)"
    if not os.path.isdir(os.path.join(root, ".git")):
        if config.git(["init", "-q"], root) is None:
            return "nested git: git init failed"
    return commit(ws, "ctx init")


def commit(ws, message):
    if not ws.get("nested_git") or not os.path.isdir(os.path.join(ws.ctx_root, ".git")):
        return "commit: skipped (nested git off)"
    root = ws.ctx_root
    config.git(["add", "-A"], root)
    out = config.git(["-c", "user.name=ctx-kit", "-c", "user.email=ctx-kit@localhost",
                      "commit", "-q", "-m", message], root)
    return "commit: %s" % ("done" if out is not None else "nothing to commit")

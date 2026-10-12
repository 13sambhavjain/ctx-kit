"""/ctx import helper: inventory existing context (zero tokens) so the model can plan a migration.

Finds CLAUDE.md / CLAUDE.local.md / .claude/CLAUDE.md / AGENTS.md, .claude/rules/*.md (not ctx-kit's),
nested CLAUDE.md files, and any extra docs folders/files given. For each: whether it loads every
session (and its token estimate), its sections, and @imports (which load eagerly).
"""
import os
import re

from . import sources, tree, util

IMPORT_RX = re.compile(r"(?:^|\s)@([~\w./-]+\.\w+|[~\w./-]+/)", re.M)
CHARS_PER_TOKEN = 3.5


def _strip_snippet(text):
    if tree.SNIP_START in text and tree.SNIP_END in text:
        a = text.index(tree.SNIP_START)
        b = text.index(tree.SNIP_END) + len(tree.SNIP_END)
        return text[:a] + text[b:]
    return text


def _sections(text):
    lines = text.splitlines()
    heads = [(i, m.group(2)) for i, l in enumerate(lines) for m in [re.match(r"^(#{1,3})\s+(.+)$", l)] if m]
    out = []
    for n, (i, h) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        size = sum(len(l) + 1 for l in lines[i:end])
        out.append((i + 1, end, h.strip(), size))
    return out


def _imports(text):
    code_free = re.sub(r"```.*?```|`[^`]*`", "", text, flags=re.S)
    return sorted(set(m.group(1) for m in IMPORT_RX.finditer(code_free)))


def candidates(ws, extra=()):
    root = ws.root
    found = []
    for rel, always in (("CLAUDE.md", True), ("CLAUDE.local.md", True), (".claude/CLAUDE.md", True), ("AGENTS.md", False)):
        if os.path.isfile(os.path.join(root, rel)):
            found.append((rel, "every session" if always else "if referenced"))
    rd = os.path.join(root, ".claude", "rules")
    if os.path.isdir(rd):
        for f in sorted(os.listdir(rd)):
            if f.endswith(".md") and not f.startswith(tree.RULE_PREFIX):
                meta, _ = util.parse_frontmatter(util.read_text(os.path.join(rd, f)))
                found.append((".claude/rules/" + f, "lazy (paths:)" if meta.get("paths") else "every session"))
    for dp, dns, fns in os.walk(root):
        depth = 0 if dp == root else util.rel(dp, root).count("/") + 1
        dns[:] = [d for d in dns if d not in sources.SKIP_DIRS and not d.startswith(".")] if depth < 4 else []
        if dp == root:
            continue
        if "CLAUDE.md" in fns:
            found.append((util.rel(os.path.join(dp, "CLAUDE.md"), root), "lazy (folder)"))
    for e in extra:
        p = os.path.join(root, e)
        if os.path.isdir(p):
            for dp, dns, fns in os.walk(p):
                dns[:] = sorted(d for d in dns if not d.startswith("."))
                for f in sorted(fns):
                    if f.endswith(".md"):
                        found.append((util.rel(os.path.join(dp, f), root), "doc"))
        elif os.path.isfile(p):
            found.append((util.rel(p, root), "doc"))
    seen, out = set(), []
    for rel, how in found:
        if rel not in seen:
            seen.add(rel)
            out.append((rel, how))
    return out


def scan(ws, extra=()):
    lines = []
    total_always = 0
    for rel, how in candidates(ws, extra):
        text = _strip_snippet(util.read_text(os.path.join(ws.root, rel)))
        toks = int(len(text) / CHARS_PER_TOKEN)
        imps = _imports(text)
        if how == "every session":
            total_always += toks
        lines.append("## %s  [%s, ~%d tokens%s]" % (rel, how, toks, ", @imports: " + ", ".join(imps) if imps else ""))
        for start, end, head, size in _sections(text):
            lines.append("   L%d-%d  ~%d tok  %s" % (start, end, int(size / CHARS_PER_TOKEN), head))
    if not lines:
        return "No existing CLAUDE.md, rules or docs found. Nothing to import."
    lines.append("")
    lines.append("Always-loaded context today: ~%d tokens per session (plus anything @imported)." % total_always)
    lines.append("@imports load eagerly at startup; converting them to plain links makes them lazy.")
    return "\n".join(lines)

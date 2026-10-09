"""Zero-token maintenance: drift, renames, broken links, duplicates; stamp; search."""
import os
import re

from . import sources, tree, util

LINK = re.compile(r"\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)")


def node_sources(meta):
    return tree.as_list(meta.get("sources"))


def check_node(ws, relnode, fix=False):
    """Return list of issues for one node; applies mechanical rename fixes if fix=True."""
    issues = []
    meta, body, text = tree.load_node(ws.ctx_root, relnode)
    stored = meta.get("source_hashes") or {}
    if not isinstance(stored, dict):
        stored = {}
    srcs = node_sources(meta)
    changed_meta = False
    new_srcs = list(srcs)
    for a in srcs:
        cur = sources.anchor_hash(ws.root, a)
        old = stored.get(a)
        if cur == "missing":
            status, cands = sources.find_moved(ws.root, a, old)
            if status == "renamed":
                if fix:
                    new = cands[0]
                    new_srcs[new_srcs.index(a)] = new
                    stored.pop(a, None)
                    stored[new] = sources.anchor_hash(ws.root, new)
                    changed_meta = True
                    issues.append(("fixed", "%s -> %s (same body, renamed/moved)" % (a, new)))
                else:
                    issues.append(("renamed", "%s -> %s (run `check --fix` to update)" % (a, cands[0])))
            elif status == "ambiguous":
                issues.append(("missing", "%s not found; candidates: %s" % (a, ", ".join(cands))))
            else:
                issues.append(("missing", "%s not found" % a))
        elif old is None:
            issues.append(("unstamped", "%s has no stored hash (run `stamp` after verifying the node)" % a))
        elif old != cur:
            issues.append(("drift", "%s changed since last_verified %s" % (a, meta.get("last_verified", "?"))))
    if changed_meta:
        meta["sources"] = new_srcs
        meta["source_hashes"] = stored
        util.atomic_write(os.path.join(ws.ctx_root, relnode), util.dump_frontmatter(meta, body))
    # links (relative to the node's folder; also accept repo-root-relative)
    base = os.path.dirname(os.path.join(ws.ctx_root, relnode))
    for m in LINK.finditer(body):
        target = m.group(1)
        if re.match(r"^[a-z]+:", target) or target.startswith("mailto:"):
            continue
        p1 = os.path.normpath(os.path.join(base, target))
        p2 = os.path.normpath(os.path.join(ws.root, target))
        if not (os.path.exists(p1) or os.path.exists(p2)):
            issues.append(("link", "broken link: %s" % target))
    return issues


def mark_status(ws, relnode, status):
    meta, body, text = tree.load_node(ws.ctx_root, relnode)
    if meta.get("status") != status and meta:
        meta["status"] = status
        util.atomic_write(os.path.join(ws.ctx_root, relnode), util.dump_frontmatter(meta, body))


def duplicates(ws, min_len=60):
    """Exact duplicate sentences (normalised) appearing in 2+ nodes."""
    seen = {}
    for r in tree.list_nodes(ws.ctx_root):
        try:
            _, body, _ = tree.load_node(ws.ctx_root, r)
        except OSError:
            continue
        body = re.sub(r"```.*?```", "", body, flags=re.S)
        # template placeholders are italic paragraphs like _Fill in..._; not real content
        body = re.sub(r"(?ms)^_[^\n].*?_\s*$", "", body)
        for sent in re.split(r"(?<=[.!?])\s+|\n+", body):
            norm = re.sub(r"[^a-z0-9]+", " ", sent.lower()).strip()
            if len(norm) < min_len:
                continue
            seen.setdefault(util.sha(norm), (sent.strip(), set()))[1].add(r)
    return [(s, sorted(nodes)) for s, nodes in seen.values() if len(nodes) > 1]


def run_check(ws, fix=False, only=None):
    """Return (report_lines, counts)."""
    lines, counts = [], {}
    nodes = only or tree.list_nodes(ws.ctx_root)
    for r in nodes:
        try:
            iss = check_node(ws, r, fix=fix)
        except OSError as e:
            iss = [("error", str(e))]
        stale = any(k in ("drift", "missing", "renamed") for k, _ in iss)
        if fix:
            meta, _, _ = tree.load_node(ws.ctx_root, r)
            if stale and meta.get("status") != "stale-suspect":
                mark_status(ws, r, "stale-suspect")
        for k, msg in iss:
            counts[k] = counts.get(k, 0) + 1
            lines.append("%-9s %s: %s" % (k.upper(), r, msg))
    if not only:
        for sent, ns in duplicates(ws):
            counts["duplicate"] = counts.get("duplicate", 0) + 1
            lines.append("DUPLICATE %s: \"%s\"" % (", ".join(ns), sent[:120]))
    if fix:
        tree.write_index(ws)
    return lines, counts


def stamp(ws, relnodes):
    """Record current source hashes + today's date: 'this node is verified against the code'."""
    out = []
    for r in relnodes:
        meta, body, _ = tree.load_node(ws.ctx_root, r)
        srcs = node_sources(meta)
        meta["source_hashes"] = {a: sources.anchor_hash(ws.root, a) for a in srcs} if srcs else None
        meta["last_verified"] = util.today()
        if meta.get("status") in (None, "draft", "stale-suspect"):
            meta["status"] = "current"
        util.atomic_write(os.path.join(ws.ctx_root, r), util.dump_frontmatter(meta, body))
        missing = [a for a, h in (meta.get("source_hashes") or {}).items() if h == "missing"]
        out.append("stamped %s%s" % (r, (" (WARNING missing: %s)" % ", ".join(missing)) if missing else ""))
    tree.write_index(ws)
    tree.write_rules(ws)
    return out


def stale_sources(ws, relnode):
    """Fast check used by the Read hook: list of anchors whose hash changed or vanished."""
    meta, _, _ = tree.load_node(ws.ctx_root, relnode)
    stored = meta.get("source_hashes") or {}
    if not isinstance(stored, dict):
        return []
    bad = []
    for a, old in stored.items():
        cur = sources.anchor_hash(ws.root, a)
        if cur != old:
            bad.append(a)
    return bad


def search(ws, query, limit=8):
    terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 1]
    if not terms:
        return []
    scored = []
    for r in tree.list_nodes(ws.ctx_root) + [tree.INDEX]:
        p = os.path.join(ws.ctx_root, r)
        if not os.path.exists(p):
            continue
        meta, body, text = tree.load_node(ws.ctx_root, r)
        summ = (meta.get("summary") or "").lower()
        title = tree.title_of(meta, body, r).lower()
        low = body.lower()
        score = 0
        for t in terms:
            score += 5 * title.count(t) + 3 * summ.count(t) + low.count(t) + 2 * r.lower().count(t)
        if score:
            hit = ""
            for line in body.splitlines():
                if any(t in line.lower() for t in terms) and line.strip():
                    hit = line.strip()[:140]
                    break
            scored.append((score, r, meta.get("summary") or "", hit))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[:limit]

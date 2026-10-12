"""Zero-token maintenance: drift (with commit attribution and diffs), renames, broken links and
backticked paths, duplicates, protected-tag removals, skipped steps; stamp; search."""
import difflib
import os
import re

from . import autocommit, config, sources, tree, util

LINK = re.compile(r"\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)")
TICKED = re.compile(r"`([^`\s]+)`")
CODE_FENCE = re.compile(r"```.*?```", re.S)


def node_sources(meta):
    return tree.as_list(meta.get("sources"))


def body_hash(body):
    return util.sha(re.sub(r"\s+", " ", body).strip())


_GIT_OK = {}


def _git_ok(ws):
    if ws.root not in _GIT_OK:
        _GIT_OK[ws.root] = config.git(["rev-parse", "--is-inside-work-tree"], ws.root) == "true"
    return _GIT_OK[ws.root]


def drift_detail(ws, anchor, stamped_commit):
    """'by commits: abc fix x; def tweak (+uncommitted)' for one anchor, or ''."""
    if not stamped_commit or not _git_ok(ws):
        return ""
    path, _ = sources.parse_anchor(anchor)
    log = config.git(["log", "--format=%h %s", "%s..HEAD" % stamped_commit, "--", path], ws.root)
    if log is None:
        return " (stamp commit %s not found)" % stamped_commit[:8]
    commits = [l[:60] for l in log.splitlines() if l.strip()]
    dirty = bool((config.git(["status", "--porcelain", "--", path], ws.root) or "").strip())
    parts = []
    if commits:
        parts.append("by commits: " + "; ".join(commits[:3]) + (" (+%d more)" % (len(commits) - 3) if len(commits) > 3 else ""))
    if dirty:
        parts.append("uncommitted changes")
    return (" — " + ", ".join(parts)) if parts else ""


def anchor_diff(ws, anchor, stamped_commit, max_lines=60):
    """Unified diff of an anchor between the stamp commit and now (symbol-level when possible)."""
    if not stamped_commit or not _git_ok(ws):
        return "(no diff: node was stamped without a git commit; re-stamp to enable)"
    path, sym = sources.parse_anchor(anchor)
    old = config.git(["show", "%s:%s" % (stamped_commit, path)], ws.root, raw=True)
    full = os.path.join(ws.root, path)
    if old is None or not os.path.isfile(full):
        return "(no diff available for %s)" % anchor
    new = util.read_text(full)
    lr = sources.line_range(sym) if sym else None
    if sym and not lr:
        old = sources.symbol_body(path, old, sym) or ""
        new = sources.symbol_body(path, new, sym) or ""
    elif lr:
        pad = 3
        old = "\n".join(old.splitlines()[max(0, lr[0] - 1 - pad):lr[1] + pad])
        new = "\n".join(new.splitlines()[max(0, lr[0] - 1 - pad):lr[1] + pad])
    d = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "%s@%s" % (anchor, stamped_commit[:8]), "%s@now" % anchor, n=2, lineterm=""))
    if len(d) > max_lines:
        d = d[:max_lines] + ["… (%d more diff lines)" % (len(d) - max_lines)]
    return "\n".join(d) or "(no textual change; whitespace only)"


EXT_RX = re.compile(r"\.[A-Za-z0-9]{1,6}$")


def _looks_like_path(ws, p):
    """Only treat a/b as a path if its first segment exists or it ends in a file extension
    (avoids application/json, and/or, owner/repo, feature/branch …)."""
    first = p.split("/", 1)[0]
    if first and (os.path.exists(os.path.join(ws.root, first)) or os.path.exists(os.path.join(ws.ctx_root, first))):
        return True
    return bool(EXT_RX.search(p.rstrip("/")))


def path_issues(ws, relnode, body):
    """Backticked repo paths (containing '/') that don't exist."""
    out = []
    seen = set()
    for m in TICKED.finditer(CODE_FENCE.sub("", body)):
        tok = m.group(1).strip().rstrip(".,;:)")
        if "/" not in tok or tok in seen or re.match(r"^[a-z]+://", tok) or tok.startswith(("~", "/", "-", "$", "<", "@")):
            continue
        if re.search(r"[*?{}<>|=]", tok):
            continue
        seen.add(tok)
        p = re.split(r"[#:]", tok, maxsplit=1)[0]
        if not p or p in (".", "..") or not _looks_like_path(ws, p):
            continue
        base = os.path.dirname(os.path.join(ws.ctx_root, relnode))
        cands = [os.path.join(ws.root, p), os.path.join(base, p), os.path.join(ws.ctx_root, p)]
        if not any(os.path.exists(c) for c in cands):
            out.append(("path", "`%s` mentioned in text but not found" % tok))
    return out


def protected_issues(ws, relnode):
    tags = ws.get("protected_tags") or []
    root = ws.ctx_root
    if not tags or not os.path.isdir(os.path.join(root, ".git")):
        return []
    head = config.git(["show", "HEAD:%s" % relnode], root, raw=True)
    if head is None:
        return []
    norm = lambda s: re.sub(r"\s+", " ", s).strip()
    tagged = [l for l in head.splitlines() if any(t in l for t in tags)]
    if not tagged:
        return []
    now = autocommit._tree_lines(root)  # a line moved to another node still counts as kept
    gone = [l.strip() for l in tagged if norm(l) not in now]
    return [("protected", "tagged line removed/changed since last commit: %s" % g[:100]) for g in gone]


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
    sc = meta.get("stamped_commit")
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
                hint = " (line anchor beyond end of file)" if sources.line_range(sources.parse_anchor(a)[1] or "") else ""
                issues.append(("missing", "%s not found%s" % (a, hint)))
        elif old is None:
            issues.append(("unstamped", "%s has no stored hash (run `stamp` after verifying the node)" % a))
        elif old != cur:
            issues.append(("drift", "%s changed since last_verified %s%s"
                           % (a, meta.get("last_verified", "?"), drift_detail(ws, a, sc))))
    if changed_meta:
        meta["sources"] = new_srcs
        meta["source_hashes"] = stored
        util.atomic_write(os.path.join(ws.ctx_root, relnode), util.dump_frontmatter(meta, body))
    # skipped steps
    if meta.get("body_hash") and meta["body_hash"] != body_hash(body):
        issues.append(("edited", "text changed since last stamp %s (verify, then stamp)" % meta.get("last_verified", "?")))
    if meta.get("status") == "current" and not meta.get("last_verified"):
        issues.append(("unverified", "status is current but the node was never stamped/verified"))
    size_kb = len(text.encode("utf-8")) / 1024.0
    if size_kb > float(ws.get("max_node_kb") or 12):
        issues.append(("large", "%.0f KB (> %s KB): split into smaller topic nodes to cut read cost" % (size_kb, ws.get("max_node_kb"))))
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
    issues += path_issues(ws, relnode, body)
    issues += protected_issues(ws, relnode)
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
        body = CODE_FENCE.sub("", body)
        # template placeholders are italic paragraphs like _Fill in..._; not real content
        body = re.sub(r"(?ms)^_[^\n].*?_\s*$", "", body)
        for sent in re.split(r"(?<=[.!?])\s+|\n+", body):
            norm = re.sub(r"[^a-z0-9]+", " ", sent.lower()).strip()
            if len(norm) < min_len:
                continue
            seen.setdefault(util.sha(norm), (sent.strip(), set()))[1].add(r)
    return [(s, sorted(nodes)) for s, nodes in seen.values() if len(nodes) > 1]


def uncommitted(ws):
    out = []
    for label, root, _ in autocommit.roots(ws):
        st = config.git(["status", "--porcelain"], root) or ""
        # porcelain lines are "XY path"; the leading space of the first line may be stripped
        out += ["%s/%s" % (label, l.strip().split(None, 1)[-1]) for l in st.splitlines() if l.strip()]
    return out


def doc_duplicates(ws, min_len=60, max_files=300):
    """Sentences that appear both in the tree and in the repo's own markdown docs."""
    tree_sents = {}
    for r in tree.list_nodes(ws.ctx_root):
        _, body, _ = tree.load_node(ws.ctx_root, r)
        for sent in re.split(r"(?<=[.!?])\s+|\n+", CODE_FENCE.sub("", body)):
            norm = re.sub(r"[^a-z0-9]+", " ", sent.lower()).strip()
            if len(norm) >= min_len:
                tree_sents.setdefault(util.sha(norm), (sent.strip(), r))
    out, n = [], 0
    ctx_abs = os.path.abspath(ws.ctx_root)
    for dp, dns, fns in os.walk(ws.root):
        dns[:] = [d for d in dns if d not in sources.SKIP_DIRS and not d.startswith(".")]
        if os.path.abspath(dp).startswith(ctx_abs) or util.rel(dp, ws.root).count("/") > 3:
            dns[:] = []
            continue
        for f in fns:
            if not f.endswith(".md") or f in ("CLAUDE.local.md",):
                continue
            n += 1
            if n > max_files:
                return out
            text = util.read_text(os.path.join(dp, f))
            for sent in re.split(r"(?<=[.!?])\s+|\n+", CODE_FENCE.sub("", text)):
                norm = re.sub(r"[^a-z0-9]+", " ", sent.lower()).strip()
                hit = tree_sents.get(util.sha(norm)) if len(norm) >= min_len else None
                if hit:
                    out.append((hit[1], util.rel(os.path.join(dp, f), ws.root), hit[0]))
    return out


def run_check(ws, fix=False, only=None, diff=False, docs=False, max_diff_lines=300):
    """Return (report_lines, counts)."""
    lines, counts = [], {}
    diff_budget = [max_diff_lines]
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
            lines.append("%-10s %s: %s" % (k.upper(), r, msg))
            if diff and k == "drift":
                if diff_budget[0] <= 0:
                    continue
                meta, _, _ = tree.load_node(ws.ctx_root, r)
                anchor = msg.split(" changed since", 1)[0]
                d = anchor_diff(ws, anchor, meta.get("stamped_commit")).splitlines()
                if len(d) > diff_budget[0]:
                    d = d[:diff_budget[0]] + ["… diff budget reached; run check --diff <node> for the rest"]
                diff_budget[0] -= len(d)
                lines.append("    " + "\n    ".join(d))
    if not only:
        for sent, ns in duplicates(ws):
            counts["duplicate"] = counts.get("duplicate", 0) + 1
            lines.append("DUPLICATE  %s: \"%s\"" % (", ".join(ns), sent[:120]))
        if docs:
            for node, doc, sent in doc_duplicates(ws):
                counts["dup-doc"] = counts.get("dup-doc", 0) + 1
                lines.append("DUP-DOC    %s and %s: \"%s\"" % (node, doc, sent[:100]))
        unc = uncommitted(ws)
        if unc:
            counts["uncommitted"] = len(unc)
            lines.append("UNCOMMITTED %d file(s) not in tree history: %s%s" % (
                len(unc), ", ".join(unc[:5]), " …" if len(unc) > 5 else ""))
    if fix:
        tree.write_index(ws)
    return lines, counts


def stamp(ws, relnodes):
    """Record source hashes, the project commit and a text hash: 'verified against the code now'."""
    out = []
    head = config.git(["rev-parse", "HEAD"], ws.root) if _git_ok(ws) else None
    for r in relnodes:
        meta, body, _ = tree.load_node(ws.ctx_root, r)
        srcs = node_sources(meta)
        meta["source_hashes"] = {a: sources.anchor_hash(ws.root, a) for a in srcs} if srcs else None
        meta["last_verified"] = util.today()
        meta["stamped_commit"] = head[:12] if head else None
        meta["body_hash"] = body_hash(body)
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


def mark_legacy_draft(ws):
    """Nodes marked current without a v0.3 stamp (no body_hash) can't be trusted as verified:
    set them to draft so they get audited. Returns the nodes changed."""
    changed = []
    for r in tree.list_nodes(ws.ctx_root):
        meta, body, _ = tree.load_node(ws.ctx_root, r)
        if meta.get("status") == "current" and not meta.get("body_hash"):
            meta["status"] = "draft"
            util.atomic_write(os.path.join(ws.ctx_root, r), util.dump_frontmatter(meta, body))
            changed.append(r)
    if changed:
        tree.write_index(ws)
    return changed

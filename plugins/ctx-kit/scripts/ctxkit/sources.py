"""Source anchors for ctx nodes: `path`, `path#Symbol`, or `dir/`.

Hashes let `/ctx check` detect drift with zero tokens, and the *name-normalised*
body hash lets it find a symbol that was renamed or moved (same body, new name).
Symbol detection is heuristic (regex + indentation/brace matching) and covers
the common languages; anything else falls back to whole-file hashing.
"""
import difflib
import os
import re

from . import util

PY_EXT = {".py", ".pyi"}
BRACE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".kts", ".cs", ".go",
             ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cxx", ".swift", ".php", ".scala", ".dart"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target",
             ".next", ".idea", ".vscode", ".claude", ".ctx", "vendor", ".gradle", "bin", "obj"}
MAX_SCAN_FILES = 4000
MAX_FILE_BYTES = 1_500_000

KW = r"(?:export\s+|default\s+|public\s+|private\s+|protected\s+|internal\s+|static\s+|final\s+|abstract\s+|async\s+|override\s+|virtual\s+|sealed\s+|partial\s+|inline\s+|pub(?:\([^)]*\))?\s+|const\s+|unsafe\s+|extern\s+)*"


def parse_anchor(anchor):
    anchor = anchor.strip()
    if "#" in anchor:
        path, sym = anchor.split("#", 1)
        return path.strip(), sym.strip()
    return anchor, None


def _py_def_lines(lines):
    rx = re.compile(r"^(\s*)(?:async\s+def|def|class)\s+([A-Za-z_]\w*)")
    for i, line in enumerate(lines):
        m = rx.match(line)
        if m:
            yield i, m.group(2), len(m.group(1).expandtabs(4))


def _py_block(lines, i, indent):
    j = i + 1
    # include decorators above
    start = i
    while start > 0 and lines[start - 1].strip().startswith("@"):
        start -= 1
    while j < len(lines):
        s = lines[j]
        if s.strip() and len(s) - len(s.lstrip()) <= indent and not s.strip().startswith((")", "]", "}")):
            break
        j += 1
    return "\n".join(lines[start:j])


def _brace_def_rx():
    return [
        re.compile(r"^\s*" + KW + r"(?:function\*?|class|interface|struct|enum|trait|impl|type|record|object|module|namespace|fn|func|def)\s+(?:\([^)]*\)\s*)?([A-Za-z_$][\w$]*)"),
        re.compile(r"^\s*" + KW + r"(?:let|var|const|val)\s+([A-Za-z_$][\w$]*)\s*[:=]\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)"),
        # methods / C-like functions: `Type name(args) {` or `name(args) {`
        re.compile(r"^\s*" + KW + r"(?:[\w<>\[\],.*&?:\s]+\s+)?([A-Za-z_$~][\w$]*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:->\s*[^{]+|:\s*[^{=]+|throws\s+[^{]+)?\{?\s*$"),
    ]


_NOT_NAMES = {"if", "for", "while", "switch", "catch", "return", "else", "do", "try", "using", "new", "sizeof", "when"}


def _brace_def_lines(lines):
    rxs = _brace_def_rx()
    for i, line in enumerate(lines):
        for rx in rxs:
            m = rx.match(line)
            if m and m.group(1) not in _NOT_NAMES:
                yield i, m.group(1), None
                break


def _brace_block(lines, i, _indent):
    depth, seen, j = 0, False, i
    while j < len(lines) and j < i + 4000:
        code = re.sub(r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|//.*$)", "", lines[j])
        for ch in code:
            if ch == "{":
                depth += 1
                seen = True
            elif ch == "}":
                depth -= 1
        j += 1
        if seen and depth <= 0:
            break
        if not seen and j - i > 3:  # declaration without a body (e.g. interface method, type alias)
            break
    return "\n".join(lines[i:j])


def _defs(path, text):
    ext = os.path.splitext(path)[1].lower()
    lines = text.splitlines()
    if ext in PY_EXT:
        return lines, list(_py_def_lines(lines)), _py_block
    if ext in BRACE_EXT:
        return lines, list(_brace_def_lines(lines)), _brace_block
    return lines, [], None


def _norm(body, name=None):
    if name:
        body = re.sub(r"\b%s\b" % re.escape(name), "\u00a7", body)
    out = [l.rstrip() for l in body.splitlines()]
    return "\n".join(l for l in out if l.strip())


def symbol_body(path, text, sym):
    """Return the body text of the (first) definition of sym, or None."""
    leaf = sym.split(".")[-1]
    lines, defs, block = _defs(path, text)
    if block is None:
        # unknown language: accept a plain textual mention as "exists", hash whole file
        return text if re.search(r"\b%s\b" % re.escape(leaf), text) else None
    for i, name, indent in defs:
        if name == leaf:
            return block(lines, i, indent)
    return None


def anchor_hash(repo_root, anchor):
    """Hash for an anchor, or 'missing'. Symbol hashes are name-normalised."""
    path, sym = parse_anchor(anchor)
    full = os.path.join(repo_root, path)
    if path.endswith("/") or os.path.isdir(full):
        if not os.path.isdir(full):
            return "missing"
        names = []
        for dp, dns, fns in os.walk(full):
            dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
            names += [util.rel(os.path.join(dp, f), full) for f in sorted(fns)]
            if len(names) > 3000:
                break
        return "d" + util.sha("\n".join(names), 11)
    if not os.path.isfile(full):
        return "missing"
    try:
        if os.path.getsize(full) > MAX_FILE_BYTES:
            st = os.stat(full)
            return "s" + util.sha("%d:%d" % (st.st_size, int(st.st_mtime)), 11)
        text = util.read_text(full)
    except OSError:
        return "missing"
    if not sym:
        return util.sha(_norm(text))
    body = symbol_body(path, text, sym)
    if body is None:
        return "missing"
    return util.sha(_norm(body, sym.split(".")[-1]))


def iter_code_files(repo_root, exts=None):
    n = 0
    for dp, dns, fns in os.walk(repo_root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(fns):
            ext = os.path.splitext(f)[1].lower()
            if exts and ext not in exts:
                continue
            if ext not in PY_EXT and ext not in BRACE_EXT:
                continue
            yield os.path.join(dp, f)
            n += 1
            if n >= MAX_SCAN_FILES:
                return


def find_moved(repo_root, anchor, old_hash):
    """For a missing anchor, look for the same (name-normalised) body elsewhere.

    Returns (status, candidates): status 'renamed' with exactly one exact match,
    'ambiguous' with several or only name-similar matches, 'gone' otherwise.
    """
    path, sym = parse_anchor(anchor)
    ext = os.path.splitext(path)[1].lower()
    if not sym:
        # whole file moved? match by content hash
        if not old_hash or old_hash == "missing":
            return "gone", []
        hits = []
        for f in iter_code_files(repo_root, {ext} if ext else None):
            try:
                if util.sha(_norm(util.read_text(f))) == old_hash:
                    hits.append(util.rel(f, repo_root))
            except OSError:
                pass
        return ("renamed", hits) if len(hits) == 1 else (("ambiguous", hits) if hits else ("gone", []))
    leaf = sym.split(".")[-1]
    exact, similar = [], []
    exts = {ext} if ext in PY_EXT or ext in BRACE_EXT else None
    for f in iter_code_files(repo_root, exts):
        try:
            text = util.read_text(f)
        except OSError:
            continue
        lines, defs, block = _defs(f, text)
        for i, name, indent in defs:
            relf = util.rel(f, repo_root)
            if old_hash and old_hash != "missing":
                h = util.sha(_norm(block(lines, i, indent), name))
                if h == old_hash:
                    exact.append("%s#%s" % (relf, name))
                    continue
            if difflib.SequenceMatcher(None, name.lower(), leaf.lower()).ratio() >= 0.8:
                similar.append("%s#%s" % (relf, name))
    if len(exact) == 1:
        return "renamed", exact
    if exact:
        return "ambiguous", exact
    if similar:
        return "ambiguous", similar[:5]
    return "gone", []

"""Small shared helpers: hashing, atomic writes, frontmatter, logging.

Standard library only; must run on Python 3.8+ (macOS, Linux, Windows).
"""
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
import traceback

FM_DELIM = "---"


def sha(text, n=12):
    if isinstance(text, str):
        text = text.encode("utf-8", "replace")
    return hashlib.sha1(text).hexdigest()[:n]


def today():
    return datetime.date.today().isoformat()


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def atomic_write(path, text):
    """Write text to path atomically (temp file + replace)."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ctxkit-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def rel(path, start):
    try:
        return os.path.relpath(path, start).replace(os.sep, "/")
    except ValueError:  # different drive on Windows
        return path.replace(os.sep, "/")


# ---------------------------------------------------------------- frontmatter
# A deliberately small YAML subset, enough for ctx nodes:
#   key: scalar
#   key: [a, b, c]
#   key:
#     - item
#   key:
#     subkey: value

def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _inline_list(v):
    inner = v.strip()[1:-1].strip()
    if not inner:
        return []
    return [_scalar(x) for x in inner.split(",") if x.strip()]


def parse_frontmatter(text):
    """Return (meta: dict, body: str). Missing/invalid frontmatter -> ({}, text)."""
    if not text.startswith(FM_DELIM):
        return {}, text
    lines = text.split("\n")
    if lines[0].strip() != FM_DELIM:
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FM_DELIM:
            end = i
            break
    if end is None:
        return {}, text
    meta = {}
    key = None
    for raw in lines[1:end]:
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and key is not None:
            s = line.strip()
            if s.startswith("- "):
                if not isinstance(meta.get(key), list):
                    meta[key] = []
                meta[key].append(_scalar(s[2:]))
            elif ":" in s:
                if not isinstance(meta.get(key), dict):
                    meta[key] = {}
                # values (hashes, short words) never contain ':'; keys might
                k, v = s.rsplit(":", 1)
                meta[key][_scalar(k)] = _scalar(v)
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            meta[key] = _inline_list(v)
        elif v == "":
            meta[key] = None
        else:
            meta[key] = _scalar(v)
    body = "\n".join(lines[end + 1:])
    if body.startswith("\n"):
        body = body[1:]
    return meta, body


def _fmt_scalar(v):
    s = str(v)
    if s == "" or re.search(r"[:#\[\]{},]|^\s|\s$", s):
        return '"' + s.replace('"', "'") + '"'
    return s


ORDER = ["title", "summary", "parent", "paths", "sources", "source_hashes",
         "last_verified", "status", "decision", "superseded_by"]


def dump_frontmatter(meta, body):
    keys = [k for k in ORDER if k in meta] + sorted(k for k in meta if k not in ORDER)
    out = [FM_DELIM]
    for k in keys:
        v = meta[k]
        if v is None:
            continue
        if isinstance(v, list):
            out.append("%s: [%s]" % (k, ", ".join(_fmt_scalar(x) for x in v)))
        elif isinstance(v, dict):
            if not v:
                continue
            out.append("%s:" % k)
            for sk in sorted(v):
                # keys like "src/a.py#Foo" contain no ':' in practice; quote if they do
                out.append("  %s: %s" % (_fmt_scalar(sk), _fmt_scalar(v[sk])))
        else:
            out.append("%s: %s" % (k, _fmt_scalar(v)))
    out.append(FM_DELIM)
    return "\n".join(out) + "\n\n" + body.lstrip("\n")


# ---------------------------------------------------------------- logging

def log_error(data_dir, where):
    """Append the current exception to <data_dir>/errors.log; never raises."""
    try:
        if not data_dir:
            return
        os.makedirs(data_dir, exist_ok=True)
        p = os.path.join(data_dir, "errors.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write("[%s] %s: %s\n" % (now_iso(), where, traceback.format_exc(limit=4).strip().replace("\n", " | ")))
        # keep the log small
        if os.path.getsize(p) > 200_000:
            lines = read_text(p).splitlines()[-500:]
            atomic_write(p, "\n".join(lines) + "\n")
    except Exception:
        pass


def eprint(*a):
    print(*a, file=sys.stderr)

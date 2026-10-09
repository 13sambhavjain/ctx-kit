"""Handoffs: folders, ids, mining offsets, pickup, /ctx-clean pairing, pre-compact checkpoints.

Layout (default .claude/handoffs/, separate from the context tree):
  <YYYYMMDD-HHMM>-<slug>-<sid8>/
      HANDOFF.md            written by Claude
      next-1-<part>.prompt.md ...
      handoff.json          {id, created, session, status: open|consumed, prompts}
  .work/<sid>/chunk-N.txt   transient mining input (deleted after use)
  .checkpoints/<sid>-<ts>.md   PreCompact safety net
Session-scoped bookkeeping (offsets, pending /ctx-clean) lives in plugin data, not the repo.
"""
import datetime
import os
import re
import shutil
import subprocess
import time

from . import config, redact, transcript, util

PENDING_TTL = 30 * 60
CLEAR_PAIR_WINDOW = 120


def _slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "session").lower()).strip("-")
    return (s or "session")[:40]


def _meta_path(folder):
    return os.path.join(folder, "handoff.json")


def list_handoffs(ws, only_open=False):
    d = ws.handoffs_dir
    out = []
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d), reverse=True):
        p = os.path.join(d, name)
        if name.startswith(".") or not os.path.isdir(p):
            continue
        meta = util.load_json(_meta_path(p), {}) or {}
        meta.setdefault("id", name)
        meta.setdefault("status", "open")
        prompts = sorted(f for f in os.listdir(p) if f.endswith(".prompt.md"))
        meta["prompts"] = prompts
        meta["folder"] = p
        if only_open and meta["status"] != "open":
            continue
        out.append(meta)
    return out


def resolve(ws, ident):
    """Find a handoff by id / unique substring; optional ':N' selects prompt N."""
    part = None
    if ident and ":" in ident:
        ident, part = ident.rsplit(":", 1)
    hs = list_handoffs(ws)
    exact = [h for h in hs if h["id"] == ident]
    cands = exact or [h for h in hs if ident and ident in h["id"]]
    if not ident:
        cands = [h for h in hs if h["status"] == "open"][:1]
    if len(cands) != 1:
        return None, None, cands
    h = cands[0]
    prompt = None
    if h["prompts"]:
        if part and part.isdigit() and 1 <= int(part) <= len(h["prompts"]):
            prompt = h["prompts"][int(part) - 1]
        else:
            prompt = h["prompts"][0]
    return h, prompt, cands


def ensure_setup(ws):
    """Return None if handoff storage preferences are known, else a question marker."""
    if ws.local is None or "handoffs_local_only" not in ws.local:
        return "FIRST_HANDOFF_IN_WORKSPACE"
    return None


def _exclude(ws, entries, header):
    excl = ws.git_dir_info()
    if not excl:
        return False
    existing = util.read_text(excl) if os.path.exists(excl) else ""
    have = set(existing.splitlines())
    add = [e for e in entries if e not in have]
    if add:
        os.makedirs(os.path.dirname(excl), exist_ok=True)
        with open(excl, "a", encoding="utf-8") as f:
            f.write(("" if existing.endswith("\n") or not existing else "\n") + header + "\n" + "\n".join(add) + "\n")
    return True


def setup(ws, local_only):
    """Record the one-time handoff preference; exclude from git when local-only."""
    ws.save({"handoffs_local_only": bool(local_only)})
    msg = "handoffs: %s" % util.rel(ws.handoffs_dir, ws.root)
    if local_only:
        entries = ["/" + util.rel(ws.handoffs_dir, ws.root) + "/", "/" + util.rel(ws.config_path, ws.root)]
        if _exclude(ws, entries, "# ctx-kit handoffs (local-only)"):
            msg += " (local-only: added to .git/info/exclude)"
        else:
            msg += " (local-only requested; not a git repo, nothing to exclude)"
    return msg


def new(ws, slug, session_id):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    sid8 = re.sub(r"[^A-Za-z0-9]", "", session_id or "nosess")[:8] or "nosess"
    name = "%s-%s-%s" % (stamp, _slug(slug), sid8)
    folder = os.path.join(ws.handoffs_dir, name)
    n = 2
    while os.path.exists(folder):
        folder = os.path.join(ws.handoffs_dir, "%s-%d" % (name, n))
        n += 1
    os.makedirs(folder)
    meta = {"id": os.path.basename(folder), "created": util.now_iso(), "session": session_id or "",
            "status": "open"}
    util.save_json(_meta_path(folder), meta)
    return folder, meta


def set_status(ws, ident, status):
    h, _, cands = resolve(ws, ident)
    if not h:
        return None
    meta = util.load_json(_meta_path(h["folder"]), {}) or {}
    meta["status"] = status
    meta["%s_at" % status] = util.now_iso()
    util.save_json(_meta_path(h["folder"]), meta)
    return h["id"]


# ------------------------------------------------------------------ mining

def _offsets_path(data):
    return os.path.join(data, "handoff-offsets.json")


def mine(ws, data, session_id, transcript_path=None, force_all=False):
    """Write chunk files for the part of the session the model may have lost.

    Returns (status, [chunk paths]). status: NO_TRANSCRIPT | NO_COMPACTION | CHUNKS | NOTHING_NEW
    Only lines before the last compaction (and after the previous handoff's offset) are mined,
    because everything after the last compaction is still in the model's context.
    """
    path = transcript.find_transcript(session_id, transcript_path)
    if not path:
        return "NO_TRANSCRIPT", []
    offsets = util.load_json(_offsets_path(data), {}) or {}
    start = int(offsets.get(session_id, 0))
    if force_all:
        end = transcript.count_lines(path)
    else:
        end = transcript.last_compaction_index(path, start)
        if end is None:
            return "NO_COMPACTION", []
    if end <= start:
        return "NOTHING_NEW", []
    entries = transcript.digest(path, start, end)
    if not entries:
        return "NOTHING_NEW", []
    work = os.path.join(ws.handoffs_dir, ".work", re.sub(r"[^A-Za-z0-9_-]", "", session_id or "x")[:64])
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    paths = []
    for n, text in enumerate(transcript.chunk(entries), 1):
        p = os.path.join(work, "chunk-%d.txt" % n)
        util.atomic_write(p, redact.redact(text)[0])
        paths.append(p)
    return "CHUNKS", paths


def mark_mined(ws, data, session_id, transcript_path=None):
    """Record how far this session has been captured, so the next handoff skips it."""
    path = transcript.find_transcript(session_id, transcript_path)
    if not path:
        return
    offsets = util.load_json(_offsets_path(data), {}) or {}
    offsets[session_id] = transcript.count_lines(path)
    util.save_json(_offsets_path(data), offsets)
    shutil.rmtree(os.path.join(ws.handoffs_dir, ".work"), ignore_errors=True)


def readiness(ws, session_id, transcript_path=None):
    hints = {"open_tasks": [], "pending_question": False, "git_dirty": None}
    path = transcript.find_transcript(session_id, transcript_path)
    if path:
        hints.update(transcript.readiness(path))
    out = config.git(["status", "--porcelain"], ws.root)
    if out is not None:
        hints["git_dirty"] = len([l for l in out.splitlines() if l.strip()])
    return hints


# ------------------------------------------------------------------ /ctx-clean pairing

def _pending_dir(data):
    return os.path.join(data, "pending")


def set_pending(ws, data, ident, session_id):
    h, prompt, cands = resolve(ws, ident)
    if not h or not prompt:
        return None
    os.makedirs(_pending_dir(data), exist_ok=True)
    util.save_json(os.path.join(_pending_dir(data), "%s.json" % _safe(session_id)), {
        "handoff": h["id"], "prompt": os.path.join(h["folder"], prompt), "folder": h["folder"],
        "cwd": ws.root, "created": time.time()})
    return h["id"]


def _safe(s):
    return re.sub(r"[^A-Za-z0-9_-]", "", s or "x")[:80]


def record_clear(data, session_id, cwd):
    """SessionEnd(reason=clear): remember which session just cleared, per workspace."""
    p = os.path.join(data, "recent-clears.json")
    items = [x for x in (util.load_json(p, []) or []) if time.time() - x.get("t", 0) < CLEAR_PAIR_WINDOW]
    items.append({"session": session_id, "cwd": os.path.normcase(os.path.abspath(cwd)), "t": time.time()})
    util.save_json(p, items[-20:])


def take_pending_after_clear(ws, data, cwd):
    """SessionStart(clear): find the /ctx-clean handoff of the session that just cleared here."""
    p = os.path.join(data, "recent-clears.json")
    items = util.load_json(p, []) or []
    key = os.path.normcase(os.path.abspath(cwd))
    now = time.time()
    for x in sorted(items, key=lambda x: -x.get("t", 0)):
        if x.get("cwd") != key or now - x.get("t", 0) > CLEAR_PAIR_WINDOW:
            continue
        pp = os.path.join(_pending_dir(data), "%s.json" % _safe(x.get("session")))
        pend = util.load_json(pp, None)
        if not pend or now - pend.get("created", 0) > PENDING_TTL:
            continue
        try:
            os.replace(pp, pp + ".done")  # atomic consume: only one new session gets it
        except OSError:
            continue
        return pend
    return None


def prompt_payload(pend_or_handoff, prompt_path):
    text = util.read_text(prompt_path) if prompt_path and os.path.exists(prompt_path) else ""
    folder = pend_or_handoff.get("folder")
    return ("ctx-kit handoff %s (continue from a previous session).\n"
            "Full handoff: %s\n\n%s" % (pend_or_handoff.get("handoff") or pend_or_handoff.get("id"),
                                        os.path.join(folder, "HANDOFF.md") if folder else "?", text.strip()))


# ------------------------------------------------------------------ PreCompact checkpoint

def checkpoint(ws, data, session_id, transcript_path=None, max_chars=60000):
    """Save a filtered digest of the session so far (zero tokens); return its path."""
    path = transcript.find_transcript(session_id, transcript_path)
    if not path:
        return None
    offsets = util.load_json(_offsets_path(data), {}) or {}
    start = int(offsets.get(session_id, 0))
    entries = transcript.digest(path, start)
    text = "\n\n".join(entries)
    if len(text) > max_chars:
        text = "[… earlier part omitted; see transcript …]\n\n" + text[-max_chars:]
    d = os.path.join(ws.handoffs_dir, ".checkpoints")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "%s-%s.md" % (_safe(session_id)[:8], datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    util.atomic_write(p, "# Pre-compaction checkpoint\n\nsession: %s\ncreated: %s\n\n%s\n"
                      % (session_id, util.now_iso(), redact.redact(text)[0]))
    # keep only the 10 newest checkpoints
    files = sorted((os.path.join(d, f) for f in os.listdir(d)), key=os.path.getmtime)
    for f in files[:-10]:
        try:
            os.remove(f)
        except OSError:
            pass
    return p

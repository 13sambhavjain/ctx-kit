"""Read Claude Code session transcripts (JSONL) defensively.

The transcript format is internal to Claude Code and changes between versions, so
everything here tolerates unknown line types and missing fields. Used for:
  - token usage (advisor)
  - handoff readiness hints (open tasks, pending question)
  - filtering the compacted-away part of a session into small text chunks for mining
"""
import glob
import json
import os
import re

from . import util

CHARS_PER_TOKEN = 3.5
CHUNK_TOKENS = 80000
SHORT_RESULT = 1500
HEAD, TAIL = 700, 400
AGENT_MAX = 6000


def claude_home():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def find_transcript(session_id, explicit=None):
    if explicit and os.path.exists(explicit):
        return explicit
    if not session_id:
        return None
    hits = glob.glob(os.path.join(claude_home(), "projects", "*", "%s.jsonl" % session_id))
    return max(hits, key=os.path.getmtime) if hits else None


def iter_lines(path, start=0):
    """Yield (index, dict) for each parseable JSON line from line index `start`."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i < start:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                yield i, d


def count_lines(path):
    n = 0
    with open(path, "rb") as f:
        for _ in f:
            n += 1
    return n


def is_compaction(d):
    if d.get("isCompactSummary") or d.get("compactMetadata"):
        return True
    st = str(d.get("subtype") or "")
    return d.get("type") in ("system", "summary") and "compact" in st


def blocks(d):
    m = d.get("message")
    if not isinstance(m, dict):
        return []
    c = m.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []


def last_usage_tokens(path):
    """Context size of the last main-thread assistant message (input + cache)."""
    last = None
    for _, d in iter_lines(path):
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        u = (d.get("message") or {}).get("usage")
        if isinstance(u, dict):
            last = u
    if not last:
        return None
    return sum(int(last.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for x in content:
            if isinstance(x, dict) and x.get("type") == "text":
                out.append(x.get("text") or "")
        return "\n".join(out)
    return ""


def _clip(s, n_head=HEAD, n_tail=TAIL):
    s = s.strip()
    if len(s) <= SHORT_RESULT:
        return s
    return s[:n_head] + "\n[… %d chars omitted …]\n" % (len(s) - n_head - n_tail) + s[-n_tail:]


def _one_line(v, n=200):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] + ("…" if len(s) > n else "")


def digest(path, start=0, end=None):
    """Turn transcript lines [start, end) into compact text entries (main thread only)."""
    tool_names = {}
    out = []
    for i, d in iter_lines(path, start):
        if end is not None and i >= end:
            break
        if d.get("isSidechain") or d.get("type") not in ("user", "assistant"):
            continue
        if d.get("isMeta") or d.get("isCompactSummary"):
            continue
        role = d["type"]
        for b in blocks(d):
            t = b.get("type")
            if t == "text":
                txt = (b.get("text") or "").strip()
                if txt and not txt.startswith("<system-reminder>"):
                    out.append("[%s] %s" % ("USER" if role == "user" else "ASSISTANT", txt))
            elif t == "tool_use":
                name = b.get("name") or "?"
                tool_names[b.get("id")] = name
                inp = b.get("input") or {}
                if name == "ExitPlanMode":
                    out.append("[PLAN]\n%s" % (inp.get("plan") or "")[:20000])
                elif name in ("TaskCreate", "TaskUpdate", "TodoWrite"):
                    out.append("[TASK %s] %s" % (name, _one_line(inp, 300)))
                elif name == "AskUserQuestion":
                    qs = [q.get("question", "") for q in (inp.get("questions") or []) if isinstance(q, dict)]
                    out.append("[ASK] " + " | ".join(qs))
                elif name in ("Agent", "Task"):
                    out.append("[AGENT CALL] %s" % _one_line(inp.get("description") or inp.get("prompt") or "", 200))
                else:
                    key = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url") or inp.get("query") or inp
                    out.append("[TOOL %s] %s" % (name, _one_line(key, 200)))
            elif t == "tool_result":
                name = tool_names.get(b.get("tool_use_id"), "?")
                txt = _text_of(b.get("content"))
                if name in ("AskUserQuestion", "ExitPlanMode"):
                    # user answers / plan feedback: always keep in full
                    out.append("[USER RESPONSE to %s] %s" % (name, txt.strip()[:20000]))
                elif name in ("Agent", "Task"):
                    out.append("[AGENT RESULT] %s" % txt.strip()[:AGENT_MAX])
                elif b.get("is_error"):
                    out.append("[ERROR %s] %s" % (name, _clip(txt, 500, 200)))
                elif txt.strip():
                    out.append("[RESULT %s] %s" % (name, _clip(txt)))
    return out


def chunk(entries, max_tokens=CHUNK_TOKENS):
    limit = int(max_tokens * CHARS_PER_TOKEN)
    chunks, cur, size = [], [], 0
    for e in entries:
        if len(e) > limit:
            e = e[:limit - 100] + "\n[… truncated …]"
        if size + len(e) > limit and cur:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += len(e) + 2
    if cur:
        chunks.append(cur)
    return ["\n\n".join(c) for c in chunks]


def last_compaction_index(path, start=0):
    idx = None
    for i, d in iter_lines(path, start):
        if is_compaction(d):
            idx = i
    return idx


def readiness(path):
    """Cheap hints for the handoff guardrail."""
    tasks = {}
    order = []
    pending_question = False
    last_text = ""
    for _, d in iter_lines(path):
        if d.get("isSidechain"):
            continue
        for b in blocks(d):
            if b.get("type") == "tool_use":
                inp = b.get("input") or {}
                if b.get("name") == "TaskCreate":
                    key = "c%d" % len(order)
                    tasks[key] = {"subject": inp.get("subject", ""), "status": "pending"}
                    order.append(key)
                elif b.get("name") == "TaskUpdate":
                    tid = str(inp.get("taskId", ""))
                    # TaskCreate ids are sequential from 1 in a session
                    if tid.isdigit() and 0 < int(tid) <= len(order):
                        k = order[int(tid) - 1]
                        if inp.get("status"):
                            tasks[k]["status"] = inp["status"]
            elif b.get("type") == "text" and d.get("type") == "assistant":
                last_text = b.get("text") or ""
    pending_question = last_text.rstrip().endswith("?")
    open_tasks = [t["subject"] for t in tasks.values() if t["status"] not in ("completed", "deleted", "cancelled")]
    return {"open_tasks": open_tasks, "pending_question": pending_question}

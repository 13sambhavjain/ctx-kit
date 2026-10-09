"""When-to-switch advisor.

Stop hook          -> one line to the USER via systemMessage (zero tokens), once per threshold
                      crossing, then every N turns; skipped while Claude is asking the user something.
UserPromptSubmit   -> once per crossing, one line of context so Claude can judge whether a unit of
                      work is done and suggest /ctx-clean at the end of its reply (few tokens, once).
Never switches sessions by itself.
"""
import json
import os
import re

from . import config, util

# Approximate cache-read price per million tokens, for an "API-equivalent" estimate only.
# Check https://platform.claude.com/docs/en/about-claude/pricing for current prices.
CACHE_READ_PER_MTOK = [
    (re.compile(r"opus"), 0.20),
    (re.compile(r"sonnet"), 0.20),
    (re.compile(r"haiku"), 0.10),
]


def _state_path(data, sid):
    return os.path.join(data, "advisor", "%s.json" % re.sub(r"[^A-Za-z0-9_-]", "", sid or "x")[:80])


def load_state(data, sid):
    return util.load_json(_state_path(data, sid), {}) or {}


def save_state(data, sid, st):
    util.save_json(_state_path(data, sid), st)


def tail_usage(path, max_bytes=3_000_000):
    """(tokens, model) from the last main-thread assistant message; reads only the file tail."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # skip partial line
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return None, None
    for line in reversed(data.splitlines()):
        if '"assistant"' not in line or '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        m = d.get("message") or {}
        u = m.get("usage")
        if isinstance(u, dict):
            t = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            return t, m.get("model")
    return None, None


def billing(ws):
    b = (ws.get("billing") or "auto").lower()
    if b in ("api", "subscription"):
        return b
    env = os.environ
    if env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_CODE_USE_BEDROCK") or env.get("CLAUDE_CODE_USE_VERTEX") \
            or env.get("CLAUDE_CODE_USE_FOUNDRY"):
        return "api"
    home = os.path.expanduser("~")
    settings = util.load_json(os.path.join(home, ".claude", "settings.json"), {}) or {}
    if settings.get("apiKeyHelper"):
        return "api"
    cj = util.load_json(os.path.join(home, ".claude.json"), {}) or {}
    if cj.get("oauthAccount"):
        return "subscription"
    return "unknown"


def _fmt_k(n):
    return "%dK" % round(n / 1000.0)


def _cost(tokens, model):
    for rx, price in CACHE_READ_PER_MTOK:
        if model and rx.search(model.lower()):
            return tokens / 1e6 * price
    return None


def level_for(ws, tokens):
    adv = ws.get("advisor") or {}
    soft, firm = int(adv.get("soft_tokens", 120000)), int(adv.get("firm_tokens", 200000))
    if tokens >= firm:
        return 2
    if tokens >= soft:
        return 1
    return 0


def enabled(ws, st):
    if "enabled" in st:
        return bool(st["enabled"])
    return bool((ws.get("advisor") or {}).get("enabled", True))


def on_stop(ws, data, inp):
    sid = inp.get("session_id") or ""
    st = load_state(data, sid)
    if not enabled(ws, st):
        return None
    path = inp.get("transcript_path")
    if not path:
        return None
    tokens, model = tail_usage(path)
    if tokens is None:
        return None
    st["turns"] = int(st.get("turns", 0)) + 1
    st["tokens"] = tokens
    lvl = level_for(ws, tokens)
    every = int((ws.get("advisor") or {}).get("every_turns", 10))
    show = False
    if lvl > int(st.get("shown_level", 0)):
        show = True
    elif lvl >= 1 and st["turns"] - int(st.get("shown_turn", 0)) >= every:
        show = True
    last = (inp.get("last_assistant_message") or "").rstrip()
    if show and last.endswith("?"):
        show = False  # Claude is waiting on the user; try again next turn
    msg = None
    if show:
        st["shown_level"] = max(lvl, int(st.get("shown_level", 0)))
        st["shown_turn"] = st["turns"]
        b = billing(ws)
        size = "context ~%s tokens" % _fmt_k(tokens)
        c = _cost(tokens, model)
        if b == "api" and c is not None:
            size += " (~$%.3f per request at cache-read prices)" % c
        elif b == "subscription":
            size += " (each request re-sends it; counts toward your usage limits)"
        elif c is not None:
            size += " (API-equivalent ~$%.3f per request)" % c
        urgency = "Good point to switch" if lvl == 1 else "Strongly consider switching"
        msg = ("ctx-kit: %s. %s once this piece of work is done: /ctx-kit:ctx-clean (handoff, clear, reload) "
               "or /ctx-kit:handoff. Silence: /ctx-kit:ctx-advisor off" % (size, urgency))
    save_state(data, sid, st)
    return msg


def on_prompt(ws, data, inp):
    sid = inp.get("session_id") or ""
    st = load_state(data, sid)
    if not enabled(ws, st) or not (ws.get("advisor") or {}).get("nudge_model", True):
        return None
    path = inp.get("transcript_path")
    tokens, _ = tail_usage(path) if path else (None, None)
    if tokens is None:
        return None
    lvl = level_for(ws, tokens)
    if lvl <= int(st.get("nudged_level", 0)):
        return None
    st["nudged_level"] = lvl
    save_state(data, sid, st)
    return ("ctx-kit advisor: this session's context is ~%s tokens and every request re-sends it. "
            "Only if your reply completes a unit of work and nothing is pending or in progress, end it with one "
            "short line suggesting /ctx-kit:ctx-clean to continue in a fresh, cheaper session. Do not interrupt "
            "ongoing work and do not mention this otherwise." % _fmt_k(tokens))


def set_session(data, sid, enabled_flag):
    st = load_state(data, sid)
    st["enabled"] = enabled_flag
    save_state(data, sid, st)

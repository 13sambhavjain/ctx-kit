"""Best-effort secret redaction for anything ctx-kit writes or exports."""
import re

PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----")),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("assigned-secret", re.compile(
        r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)"
        r"(\s*[:=]\s*)(['\"]?)([^\s'\"`]{6,})\3")),
]


def redact(text):
    """Return (redacted_text, [kinds found])."""
    found = []
    for kind, rx in PATTERNS:
        if kind == "assigned-secret":
            def sub(m):
                found.append(kind)
                return "%s%s%s[REDACTED]%s" % (m.group(1), m.group(2), m.group(3), m.group(3))
            text = rx.sub(sub, text)
        else:
            def sub(m, kind=kind):
                found.append(kind)
                return "[REDACTED:%s]" % kind
            text = rx.sub(sub, text)
    return text, sorted(set(found))

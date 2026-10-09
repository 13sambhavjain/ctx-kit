"""Workspace discovery and configuration.

Precedence: built-in DEFAULTS < ~/.ctx-kit/config.json < <workspace>/.claude/ctx-kit.json
The workspace config file existing is what "ctx-kit is set up here" means.
"""
import os
import subprocess

from . import util

CONFIG_REL = ".claude/ctx-kit.json"
HOME_DIR = os.path.join(os.path.expanduser("~"), ".ctx-kit")
GLOBAL_CONFIG = os.path.join(HOME_DIR, "config.json")

DEFAULTS = {
    "version": 1,
    "storage": "workspace",      # workspace -> .claude/ctx | dotctx -> .ctx | home -> ~/.ctx-kit/trees/<id>
    "local_only": True,          # excluded from git via .git/info/exclude (asked at init)
    "nested_git": False,         # history for the tree itself (asked at init)
    "write_mode": "script",      # script (ctxw via Bash) | tool (Claude's Write/Edit)
    "review": "ask",             # /ctx update shows proposed changes first
    "rules": True,               # generate .claude/rules/ctx-*.md for nodes with paths:
    "handoffs_dir": ".claude/handoffs",
    "autoload_on_clear": False,  # experimental (M2)
    "advisor": {"enabled": True, "soft_tokens": 120000, "firm_tokens": 200000, "every_turns": 10},
    "billing": "auto",           # auto | api | subscription
}


def git(args, cwd, timeout=10):
    """Run git; return stdout (stripped) or None if git is missing/fails."""
    try:
        r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def has_git():
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def find_workspace(start=None):
    """Nearest ancestor with .claude/ctx-kit.json, else git toplevel, else start."""
    start = os.path.abspath(start or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    d = start
    while True:
        if os.path.isfile(os.path.join(d, CONFIG_REL)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    top = git(["rev-parse", "--show-toplevel"], start)
    if top:
        return os.path.abspath(top)
    return start


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Workspace:
    def __init__(self, root=None):
        self.root = find_workspace(root)
        self.config_path = os.path.join(self.root, CONFIG_REL)
        self.local = util.load_json(self.config_path, None)
        self.cfg = _merge(_merge(DEFAULTS, util.load_json(GLOBAL_CONFIG, {})), self.local or {})

    @property
    def initialized(self):
        return self.local is not None

    def get(self, key, default=None):
        return self.cfg.get(key, default)

    def repo_id(self):
        common = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], self.root)
        anchor = os.path.dirname(common) if common and common.endswith(".git") else (common or self.root)
        name = os.path.basename(os.path.normpath(anchor)) or "workspace"
        return "%s-%s" % (name, util.sha(os.path.normcase(os.path.abspath(anchor)), 8))

    @property
    def ctx_root(self):
        s = self.cfg.get("storage", "workspace")
        if s == "home":
            return os.path.join(HOME_DIR, "trees", self.repo_id())
        if s == "dotctx":
            return os.path.join(self.root, ".ctx")
        return os.path.join(self.root, ".claude", "ctx")

    @property
    def handoffs_dir(self):
        return os.path.join(self.root, self.cfg.get("handoffs_dir") or ".claude/handoffs")

    def save(self, updates):
        data = dict(self.local or {})
        data.update(updates)
        data.setdefault("version", 1)
        util.save_json(self.config_path, data)
        self.local = data
        self.cfg = _merge(_merge(DEFAULTS, util.load_json(GLOBAL_CONFIG, {})), data)

    def git_dir_info(self):
        """Path to info/exclude for this repo (worktree-safe) or None."""
        common = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], self.root)
        if not common:
            return None
        return os.path.join(common, "info", "exclude")

    def worktree_count(self):
        out = git(["worktree", "list", "--porcelain"], self.root)
        if not out:
            return 0
        return sum(1 for line in out.splitlines() if line.startswith("worktree "))


def plugin_data_dir(explicit=None):
    d = explicit or os.environ.get("CLAUDE_PLUGIN_DATA")
    if not d:
        d = os.path.join(HOME_DIR, "data")
    return d


def plugin_root():
    return os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

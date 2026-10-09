#!/usr/bin/env python3
"""read-guard entry: rg.py hook <read|bash|pre-compact|session-start> | rg.py [--data DIR] <stats|on|off|reset-stats|bash-observe on|off>"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if sys.version_info < (3, 8):
    sys.exit(0)
from readguard import core  # noqa: E402

args = sys.argv[1:]
if "--data" in args:
    i = args.index("--data")
    if i + 1 < len(args) and args[i + 1]:
        os.environ["CLAUDE_PLUGIN_DATA"] = args[i + 1]
    del args[i:i + 2]
if args[:1] == ["hook"]:
    sys.exit(core.hook_main(args[1] if len(args) > 1 else ""))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.exit(core.cli(args))

#!/usr/bin/env python3
"""Entry point: python ctx.py <command> ... (see ctxkit/cli.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if sys.version_info < (3, 8):
    sys.stderr.write("ctx-kit needs Python 3.8+\n")
    sys.exit(0)

from ctxkit.cli import main  # noqa: E402

sys.exit(main())

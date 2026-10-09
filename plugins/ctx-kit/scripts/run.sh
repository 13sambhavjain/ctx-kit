#!/bin/sh
# ctx-kit launcher: finds a working Python 3 (macOS, Linux, Git Bash on Windows)
# and runs scripts/ctx.py. Override with CTXKIT_PYTHON or the plugin option python_cmd.
DIR=$(cd "$(dirname "$0")" && pwd)
PY="${CTXKIT_PYTHON:-$CLAUDE_PLUGIN_OPTION_PYTHON_CMD}"
if [ -z "$PY" ]; then
  # `python3` on Windows may be the Microsoft Store stub, so test that it really runs.
  for c in python3 python "py -3"; do
    if $c -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1; then
      PY="$c"; break
    fi
  done
fi
if [ -z "$PY" ]; then
  echo "ctx-kit: Python 3.8+ not found. Install Python or set CTXKIT_PYTHON / the plugin's python_cmd option." >&2
  exit 0
fi
exec $PY "$DIR/ctx.py" "$@"

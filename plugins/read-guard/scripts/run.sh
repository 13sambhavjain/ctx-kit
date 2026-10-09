#!/bin/sh
# read-guard launcher: finds a working Python 3 (macOS, Linux, Git Bash on Windows).
DIR=$(cd "$(dirname "$0")" && pwd)
PY="${READGUARD_PYTHON:-$CLAUDE_PLUGIN_OPTION_PYTHON_CMD}"
if [ -z "$PY" ]; then
  for c in python3 python "py -3"; do
    if $c -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1; then
      PY="$c"; break
    fi
  done
fi
[ -z "$PY" ] && exit 0
exec $PY "$DIR/rg.py" "$@"

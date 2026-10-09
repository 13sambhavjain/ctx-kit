#!/bin/sh
# PostToolUse(Read) fast path: only start Python when the read file looks like
# a ctx-kit node (path contains "ctx"). Every other Read costs ~nothing.
IN=$(cat)
case "$IN" in
  *ctx*) printf '%s' "$IN" | sh "$(dirname "$0")/run.sh" hook post-read ;;
esac
exit 0

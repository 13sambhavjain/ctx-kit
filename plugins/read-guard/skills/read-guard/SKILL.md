---
name: read-guard
description: Show read-guard's savings statistics, or turn it on or off.
argument-hint: stats | on | off | reset-stats | bash-observe on|off
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

Run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" --data "${CLAUDE_PLUGIN_DATA}" $ARGUMENTS`
(use `stats` if no argument was given) and show the output to the user as-is.

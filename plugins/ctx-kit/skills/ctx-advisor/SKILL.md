---
name: ctx-advisor
description: Turn the ctx-kit session-size advisor on or off, show its status, or set its token thresholds.
argument-hint: on | off | status | threshold <soft> [firm] | on --global | off --global
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

Run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" advisor $ARGUMENTS --session ${CLAUDE_SESSION_ID}`
and relay the result in one line. `on`/`off` apply to this session only, unless `--global` is given
(then they change the default for all sessions). Thresholds are in tokens, e.g. `threshold 150000 250000`.

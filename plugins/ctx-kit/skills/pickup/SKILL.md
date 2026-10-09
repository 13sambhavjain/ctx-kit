---
name: pickup
description: Continue from a ctx-kit handoff in this (new) session. Lists open handoffs when no id is given.
argument-hint: [handoff-id[:N]]
allowed-tools: Read Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

# ctx-kit: pickup

**CTX** = `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh"`. Requested: `$ARGUMENTS`

1. If no id was given: `CTX handoff list --open`. If there is exactly one, use it; otherwise ask the user which one.
2. `CTX handoff show <id[:N]>`. It prints the prompt and the HANDOFF.md path.
3. Read HANDOFF.md, but only the sections the prompt points to, plus "Next steps" and "Working set".
   Read working-set files only when you actually need them.
4. `CTX handoff consume <id>`.
5. Tell the user in one line what you're continuing with, then follow the prompt.

---
name: ctx-clean
description: A better compact. Writes a full handoff, clears the session, and loads the handoff into the fresh session automatically. Use when the context is large and the current piece of work is done.
argument-hint: [slug] [--force]
disable-model-invocation: true
allowed-tools: Read Grep Glob Agent Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

# ctx-kit: ctx-clean (handoff, clear, reload)

**CTX** = `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh"`. Arguments: `$ARGUMENTS`. Session: `${CLAUDE_SESSION_ID}`.

1. Invoke the `ctx-kit:handoff` skill (Skill tool) with the same arguments and complete it, writing
   exactly ONE `next-1-*.prompt.md`. If its readiness check says work is unfinished and there's no
   `--force`, stop there and explain; don't clear.
2. `CTX handoff pending <id> --session ${CLAUDE_SESSION_ID}`. This arms the reload for THIS session's next clear only.
3. Clear the session:
   - If a tool that clears this session is available (in the Claude desktop app: `clear_session`
     with `session_id: "self"`; load it with ToolSearch if it's deferred), call it. The clear happens
     when this turn ends, and the app may ask the user to approve it.
   - Otherwise tell the user: "Type /clear. The handoff loads into the new session automatically."
4. Your final message: one line saying the handoff `<id>` is saved and will load after the clear.

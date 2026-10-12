---
name: ctx-feedback
description: Save a ctx-kit observation, bug or idea on this machine and show all saved notes as one compact, photo-friendly screen. Diagnostics only (no code, file contents, project names or paths); nothing is sent anywhere.
argument-hint: [what happened or what to improve]
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

# ctx-kit feedback

Zero-token alternative the user can use instead of this skill: type `ctx-note: <text>` in the chat
to save a note, and `ctx-notes` to show them all. A hook handles it and Claude never sees it.

Note from the user: `$ARGUMENTS`

1. Choose the kind: `bug`, `idea` or `observation`.
2. If this session saw relevant ctx-kit behaviour (a hook error, a permission prompt, a wrong check
   result, a skipped step, something slow), you may add one short sentence about it. Describe
   **only plugin behaviour**: never code, file contents, project or company names, paths, or
   anything from the user's work.
3. Run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" feedback --kind <kind> --note "<note>" --data "${CLAUDE_PLUGIN_DATA}"`.
4. Show the printed report exactly as-is, and tell the user they can photograph it. Notes stay on this
   machine (`ctx-notes clear` archives them). Never send, post or open anything yourself.

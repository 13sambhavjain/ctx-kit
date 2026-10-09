---
name: ctx-feedback
description: Draft a ctx-kit bug report, idea or observation as a GitHub issue, with diagnostics only (no code, file contents, project names or paths). The user reviews it and files it themselves.
argument-hint: [what happened or what to improve]
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

# ctx-kit feedback

Note from the user: `$ARGUMENTS`

1. Choose the kind: `bug` (something broke), `idea` (a suggestion) or `observation` (anything else).
2. If this session saw relevant ctx-kit behaviour (a hook error, a permission prompt, a wrong
   drift result, something slow), you may add it to the note. Describe **only plugin
   behaviour**. Never include code, file contents, project or company names, paths,
   or anything from the user's work.
3. Run:
   `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" feedback --kind <kind> --note "<note>" --data "${CLAUDE_PLUGIN_DATA}"`
4. Show the user the full draft exactly as printed and ask them to check it. If they want
   changes, rerun with the edited note.
5. Give them the printed link (or the saved draft path). **They open it and submit it
   themselves.** Never submit, post or open anything yourself.

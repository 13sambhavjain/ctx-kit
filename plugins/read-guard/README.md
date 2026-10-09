# read-guard

Claude often re-reads files it already has in context, and every one of those tokens is then
re-sent on every later request in the session. read-guard (hooks only, zero tokens itself):

| Situation | What Claude gets instead of the full file |
|---|---|
| Re-read of an unchanged file/range it already saw in this context | a one-line "unchanged, use your earlier read" stub |
| Whole-file re-read after the file changed (Claude's edits or outside changes) | only a unified diff (when it's well under 60% of the full text) |
| Claude Code's native "file unchanged" stub, but the file **did** change outside Claude (a known native bug) | the fresh content |
| Claude repeats the exact same Read right after a stub/diff | the full text (escape hatch, e.g. after it lost the content) |

It forgets everything at compaction and `/clear`, keeps subagents separate from the main
conversation, skips small files (<300 chars), images and PDFs, and observes (never changes)
repeated read-like Bash commands, so you can see whether that's worth handling too.

**Safe by design:** replacements use Claude Code's `updatedToolOutput` in the Read tool's own
output shape. If a Claude Code version rejects the shape, the original output is used: no
savings, but no breakage. Any internal error also falls back to the normal output.

## Commands
`/read-guard stats` · `/read-guard on|off` · `/read-guard reset-stats` · `/read-guard bash-observe on|off`

## Requirements
Python 3.8+ and `sh` (Git Bash on Windows), as for ctx-kit. Independent of ctx-kit; works with it.

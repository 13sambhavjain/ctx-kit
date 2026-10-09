---
name: handoff
description: Write a lossless handoff of this session (goal, state, decisions and reasons, open questions, working set) plus ready-to-paste prompt(s) for the next session(s). Use when the user says handoff, or when wrapping up before starting a fresh session.
argument-hint: [slug] [--force] [--split]
allowed-tools: Read Grep Glob Agent Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

# ctx-kit: handoff

Arguments: `$ARGUMENTS`. Session: `${CLAUDE_SESSION_ID}`.
**CTX** = `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh"` (always type it exactly so approvals keep matching).

## 1. Readiness (skip if `--force`)
Run `CTX handoff readiness --session ${CLAUDE_SESSION_ID}`. Then use your own judgement too: is
anything mid-way (an edit half-applied, a command still running, tests not yet run after a change,
a question to the user unanswered)? If work is unfinished, say briefly what, suggest finishing it
first, and STOP. The user can rerun with `--force`. `git_dirty` is only a hint; uncommitted work is fine.

## 2. First handoff in this workspace?
`CTX handoff check-setup`. If it prints `FIRST_HANDOFF_IN_WORKSPACE`, ask the user once:
"Keep handoffs local-only (not committed)?" (recommended: yes). Then run
`CTX handoff setup --local-only y|n`.

## 3. Recover what may have been lost to compaction (cost-aware)
`CTX handoff mine --session ${CLAUDE_SESSION_ID}`.
- `NO_COMPACTION`, `NOTHING_NEW` or `NO_TRANSCRIPT`: you already have the whole session in context, so skip mining.
- `CHUNKS` + paths: for each chunk file, use the Agent tool (in parallel) with
  `subagent_type: "ctx-kit:transcript-miner"` and the chunk path. Merge their lists into your own
  view of the session. Anything they found that you had lost must go into the handoff.

## 4. Durable knowledge goes to the tree (only if a ctx tree exists here)
If `CTX status --brief` shows a tree: durable facts and decisions (with reasons) belong in the tree,
not only in the handoff. Record them as in `/ctx update` (with its review step), then link to those
nodes and decisions from the handoff instead of repeating them. Without a tree, put everything in the handoff.

## 5. Write the handoff
`CTX handoff new --slug <slug or short topic> --session ${CLAUDE_SESSION_ID}` prints the `id`.
Write `<id>/HANDOFF.md` with `CTX w --handoffs write <id>/HANDOFF.md <<'EOF' ... EOF`:

```
# Handoff: <topic>  (<id>)
## Goal
## Status: done / in progress / not started
## Decisions and why            (who decided; link tree decisions if any)
## Rejected options and why
## User preferences and instructions given this session
## Open questions
## Gotchas and facts learned    (link tree nodes when they hold the fact)
## Working set                  path[:lines] or path#Symbol — what matters there and why
## Next steps                   numbered, concrete
```
Point to files and nodes instead of pasting their content. No secrets (writes are redacted anyway).

Then write one prompt per next session: `<id>/next-1-<part>.prompt.md`, plus more with `--split`
when the remaining work divides into independent parts. Each prompt is short and self-contained:
the goal, "read <handoffs path>/<id>/HANDOFF.md first (sections …)", the first concrete step, and constraints.

## 6. Finish
`CTX handoff done <id> --session ${CLAUDE_SESSION_ID}`, so a later handoff in this session only
mines what's new. Tell the user in 2-3 lines: the handoff path, and how to continue:
`/ctx-kit:pickup <id>` (or `<id>:2` for the second prompt) in a new session, or `/ctx-kit:ctx-clean`
next time to hand off, clear and reload in one step.

---
name: ctx
description: Project context tree (ctx-kit). Use to set up a tree (init), record durable learnings - facts, flows, interfaces, decisions and their reasons - so future sessions don't re-research (update), check or fix drift between nodes and code (check, audit), or find a node (search).
argument-hint: init | update ["fact" @file ...] | check | audit | search <words> | stamp <node...> | status
allowed-tools: Read Grep Glob Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

# ctx-kit: context tree

Current state:
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" status --brief`

Request: `$ARGUMENTS` (empty = `status` + short help)

**CTX** below means exactly this command prefix. Always type it this way so the
user's "don't ask again" approval keeps matching:
`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh"`

Keep token use low: never read the whole tree. Read `INDEX.md`, then only the nodes
you need. Prefer `CTX search <words>` to Grep (Grep skips git-excluded files, so a
local-only tree is invisible to a repo-wide Grep; if you must Grep, pass the tree path).

## init
1. If state says it's already initialized, say so and offer `CTX config set <key> <value>` changes.
2. Ask the user in ONE AskUserQuestion call:
   - **Storage**: `workspace` = `.claude/ctx` (default) | `dotctx` = `.ctx/` (no permission prompts at all) | `home` = `~/.ctx-kit/trees/<repo>` (shared by all git worktrees).
   - **Local-only?** yes (default; not committed, paths added to `.git/info/exclude`) | no (committed with the project; teammates see it).
   - **History for the tree** (nested git repo inside the tree)? no (default) | yes. Only meaningful when local-only or home.
   - **Write mode**: `script` (default; Claude writes via CTX, approve once with "Yes, and don't ask again") | `tool` (Claude's Write/Edit; one "allow .claude edits this session" click per session).
3. Read the project README (first ~60 lines only, if present) and draft a 2-3 sentence overview.
4. Run `CTX init --storage <s> --local-only y|n --nested-git y|n --write-mode <m> --overview "<overview>"` and relay its output (including any WARNING).
5. Tell the user: nodes start as stubs and fill in as you work (`/ctx update`). Do NOT fill every node now; offer a deeper pass only if asked, with an estimate first (it reads a lot of code).

## Writing nodes
- **script mode**: `CTX w write|append|replace|mv|rm <path-in-tree> <<'EOF' ... EOF`. Paths are relative to the tree root. Never use Write/Edit on tree files in this mode.
  - Small edits use `replace` blocks (several per call allowed); every OLD must match exactly once:
    ```
    <<<<<<< OLD
    exact current text
    =======
    new text
    >>>>>>> NEW
    ```
  - If CTX is blocked or prompts on every call even after "don't ask again": run `CTX config set write_mode tool`, tell the user, and continue in tool mode.
- **tool mode**: Write/Edit files under the tree path, then run `CTX index`.
- Node frontmatter: `summary` (one line, shown in INDEX), `parent`, `paths` (code globs this node covers; generates a lazy `.claude/rules` pointer), `sources` (`path`, `path#Symbol` or `dir/` the facts come from), `status`.
- One topic per node. Each fact lives in ONE node; elsewhere link to it with a relative markdown link. Prefer extending an existing node over creating a new one.
- **Decisions** (ADR): `decisions/NNNN-slug.md` (next free number) with `title: "D-NNNN: ..."`, `decision: accepted|proposed`, body: Date, Who (user / model / session), Decision, Why, Alternatives, Affects (links). Add a row to `decisions/DECISIONS.md`. Never rewrite an accepted decision: write a new one, then set the old one's `decision: superseded` and `superseded_by: D-xxxx`.
- Never write secrets or credentials (writes are redacted anyway).
- After writing nodes you verified against the code: `CTX stamp <node...>`.

## update ["fact" @file ...]
1. **Collect** candidate learnings: from the arguments if given; otherwise from this session: durable facts, flows, interfaces, decisions and their reasons, gotchas. Skip transient detail. `CTX touched --data "${CLAUDE_PLUGIN_DATA}"` lists files this session edited.
2. **Route** each item: read `INDEX.md` (node list + routing table); pick the existing node that owns the topic; create a new node only when nothing fits, under the right folder (`protocols/<name>/`, `modules/`, `api/`, ...). Read the target node before writing to avoid duplicates; merge.
3. **Review**: if config `review` is `ask` (default), show a compact table (item → node, new/edit → one-line change) and ask the user to approve, edit or skip items. If `auto`, proceed.
4. **Apply** with small `replace` blocks, not whole-file rewrites.
5. `CTX stamp <changed nodes>`, then `CTX commit -m "ctx update: <topic>"` (does nothing unless nested git is on).
6. Report in 1-3 lines: nodes changed and created.

## check
Run `CTX check`. RENAMED → run `CTX check --fix` (mechanical, zero tokens). DRIFT/MISSING → offer `/ctx audit`. DUPLICATE → propose keeping it in one node and linking from the other. UNSTAMPED → stamp after verifying. BROKEN LINK → fix the link.

## audit
1. `CTX check --fix`; collect nodes with DRIFT or MISSING.
2. For up to ~5 nodes per call, use the Agent tool with `subagent_type: "ctx-kit:ctx-auditor"`. Pass the workspace root, the node paths and the changed anchors.
3. Apply the mechanical fixes it returns via `CTX w replace`. For behaviour or meaning changes, show the user and ask first. Then `CTX stamp` the fixed nodes.

## search <words>
`CTX search <words>`, then Read only the hits you need.

## stamp <node...> | status
Run directly and relay the output briefly.

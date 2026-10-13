---
name: ctx
description: Project context tree (ctx-kit). Use to set up a tree (init), adopt an existing CLAUDE.md or docs folder (import), record durable learnings - facts, flows, interfaces, decisions and their reasons - so future sessions don't re-research (update), check or fix drift between nodes and code (check, audit), or find a node (search).
argument-hint: init | upgrade | import [docs-path ...] | update ["fact" @file ...] | check [--diff] | audit | search <words> | stamp <node...> | status
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
   - **Local-only?** yes (default; not committed, paths added to `.git/info/exclude`) | no (committed with the project; team use isn't fully supported yet).
   - **History** (a nested git repo inside the tree, auto-committed at the end of every turn)? yes (recommended when local-only) | no.
   - **Write mode**: `script` (default; Claude writes via CTX, approve once with "Yes, and don't ask again") | `tool` (Claude's Write/Edit; one "allow .claude edits this session" click per session).
3. Read the project README (first ~60 lines only, if present) and draft a 2-3 sentence overview.
4. Run `CTX init --storage <s> --local-only y|n --nested-git y|n --write-mode <m> --overview "<overview>"` and relay its output (including any WARNING or NOTE).
5. If `CTX import-scan` shows an existing CLAUDE.md, rules or docs with real content, offer `/ctx import`.
6. Tell the user: nodes start as stubs and fill in as you work (`/ctx update`). Do NOT fill every node now.
   Optional: protect the user's own lines with a tag, e.g. `CTX config set protected_tags '["[SJ]"]'`.

## upgrade
Use when the state above shows UPGRADE NOTES, or when asked. The notes are computed from this project,
so they're correct whatever version it was set up with.
1. `CTX upgrade`. Show the user the numbered notes in plain words, and ask which to apply (one
   AskUserQuestion call, multi-select). Never apply them silently: they change config, node status or files.
2. Run the chosen commands with the CTX prefix (e.g. `CTX mark-legacy-draft`). For "history", run
   `CTX config set nested_git true`, then `CTX init` with the project's existing settings (see `CTX config get`).
3. `CTX upgrade --done` records the version so the notes stop appearing, even if some were declined.

## import [docs-path ...]
Adopt existing context without losing or silently "verifying" it.
1. `CTX import-scan [docs-path ...]`. It lists files, whether they load every session (with a token
   estimate), their sections and their `@imports`. Show the user a short summary and ask which sources to import.
2. For each chosen section, pick the target node (route by INDEX; one topic per node; folders like
   `protocols/<name>/`, `modules/`, `api/`). Write it with frontmatter `status: draft` and
   `imported_from: <file>#<heading>`. Imported text is **never** `current` until verified against the
   code and stamped. Convert old links and names to the new node paths, list any you couldn't convert,
   and don't carry over stale link descriptions.
3. Facts already present in the tree: merge, don't duplicate. Decisions: create `decisions/NNNN-*.md` (ADR).
4. Shared files (`CLAUDE.md`, `.claude/rules/*`) are the user's: **ask before editing them.** If they
   agree, replace each moved section with a one-line pointer to its node, and turn `@imports` into plain
   links, because @imports load at every startup.
5. `CTX check` (it flags dead paths and links from the old docs), then report: nodes created, what
   stayed where it was, and that the imported nodes are drafts to audit gradually (`/ctx audit`).

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
- **tool mode**: Write/Edit files under the tree path. **Then always run `CTX index`, and `CTX stamp` the nodes you verified.** `check` reports nodes edited but not stamped, so skipping this shows up.
- **Protected lines**: lines with a tag listed in `protected_tags` (e.g. `[SJ]`) are the user's own words.
  Never remove or reword them. If one is genuinely wrong, ask the user; only then pass `--allow-protected`.
  When you record something the user decided or stated, prefix it with their tag if one is configured.
- History is committed automatically at the end of each turn when nested git is on. Use
  `CTX commit -m "<what and why>"` after a meaningful update for a better message than the automatic one.
- Node frontmatter: `summary` (one line, shown in INDEX), `parent`, `paths` (code globs this node covers; generates a lazy `.claude/rules` pointer), `sources` (`path`, `path#Symbol`, `path#L10-L20` or `dir/`; prefer `#Symbol`, since it survives edits elsewhere and follows renames), `status`.
- One topic per node, ideally under ~12 KB. Each fact lives in ONE node; elsewhere link to it. Prefer extending an existing node over creating a new one.
- **Decisions** (ADR): `decisions/NNNN-slug.md` (next free number) with `title: "D-NNNN: ..."`, `decision: accepted|proposed`, body: Date, Who (user / model / session), Decision, Why, Alternatives, Affects (links). Add a row to `decisions/DECISIONS.md`. Never rewrite an accepted decision: write a new one, then set the old one's `decision: superseded` and `superseded_by: D-xxxx`.
- Never write secrets or credentials (writes are redacted anyway).

## update ["fact" @file ...]
1. **Collect** candidate learnings: from the arguments if given; otherwise from this session: durable facts, flows, interfaces, intent, decisions and their reasons, gotchas, findings. Skip transient detail. `CTX touched --session ${CLAUDE_SESSION_ID} --data "${CLAUDE_PLUGIN_DATA}"` lists files this session edited.
2. **Route** each item: read `INDEX.md` (node list + routing table); pick the existing node that owns the topic; create a new node only when nothing fits. Read the target node before writing to avoid duplicates; merge.
3. **Review**: if config `review` is `ask` (default), show a compact table (item → node, new/edit → one-line change) and ask the user to approve, edit or skip items. If `auto`, proceed.
4. **Apply** with small `replace` blocks (tool mode: Edit), not whole-file rewrites.
5. `CTX index`, `CTX stamp <changed nodes you verified>`, then `CTX commit -m "ctx update: <topic>"`.
6. Report in 1-3 lines: nodes changed and created, decisions recorded.

## check [--diff]
Run `CTX check` (add `--diff` to see what changed in drifted code). It reports:
- RENAMED: run `CTX check --fix`.
- DRIFT (with the commits that caused it) / MISSING: offer `/ctx audit`.
- EDITED / UNVERIFIED: verify against the code, then stamp.
- PATH / BROKEN LINK: fix the reference.
- PROTECTED: a tagged line was removed; restore it or ask the user.
- DUPLICATE: keep it in one node and link from the other.
- LARGE: propose a split.
- UNCOMMITTED: commit.

## audit
1. `CTX check --fix --diff`; collect nodes with DRIFT, MISSING, EDITED or UNVERIFIED.
2. For up to ~5 nodes per call, use the Agent tool with `subagent_type: "ctx-kit:ctx-auditor"`. Pass the workspace root, the node paths, the changed anchors and the diffs.
3. Apply the mechanical fixes it returns via `CTX w replace`. For behaviour or meaning changes, show the user and ask first. Then `CTX stamp` the fixed nodes.

## search <words>
`CTX search <words>`, then Read only the hits you need.

## stamp <node...> | status
Run directly and relay the output briefly.

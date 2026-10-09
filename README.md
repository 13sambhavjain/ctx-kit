# ctx-kit (and read-guard)

This repo is the `sambhav-plugins` marketplace with two independent plugins:
- **ctx-kit**: context tree, handoffs, session-size advisor (below)
- **read-guard**: stop paying twice for file contents ([plugins/read-guard](plugins/read-guard/README.md))

---

A Claude Code plugin that keeps your project's context in a **tree of small markdown
files that Claude loads only when it needs them**. Lower token cost per session, and a
readable, versionable record of how the project works and why decisions were made.

> Status: **v0.2**: context tree, handoff, and session-size advisor. Each part works on its own.

## What you get

- **`INDEX.md` + topic nodes.** A tiny pointer in `CLAUDE.md` (or `CLAUDE.local.md`) leads
  to `INDEX.md`, which lists every node with a one-line summary and a routing table
  (code path → node). Nodes are plain links, never `@imports`, so they cost nothing until opened.
- **Automatic branch loading.** A node with `paths:` gets a generated
  `.claude/rules/ctx-*.md` pointer that Claude Code loads lazily, only when files under
  those paths are read or edited.
- **Decisions as ADRs.** `decisions/NNNN-*.md`, never edited once accepted; a newer one
  supersedes an older one. Other nodes link to decisions instead of repeating the reasoning.
- **Zero-token maintenance** (Python scripts, no model calls):
  - *drift*: each node records hashes of the code it describes (`sources: [path#Symbol]`);
  - *renames/moves*: a renamed or moved function with the same body is found and the
    node's anchor fixed automatically;
  - broken links and duplicated sentences across nodes.
- **Stale warning while working.** When Claude opens a node whose code has changed since it
  was last verified, a hook adds one line telling it to verify first.
- **`/ctx update`** turns what you learned this session into node edits, with a review
  step, merging into existing nodes rather than duplicating.
- **`/ctx audit`** runs a cheap Haiku subagent only on drifted nodes; it fixes mechanical
  things (names, paths, signatures) and only *reports* changes in meaning.
- **Safe writes.** Claude writes through a small script that only accepts paths inside the
  tree, refuses config-like files, redacts secrets, and won't overwrite on stale edits.

### Handoff (works with or without a tree)

- **`/handoff [slug] [--force] [--split]`** writes `.claude/handoffs/<date>-<slug>-<session>/HANDOFF.md`
  (goal, status, decisions + why, rejected options, your instructions, open questions, gotchas,
  working set, next steps) and one or more `next-N-*.prompt.md` prompts for the next session(s).
  - **Guardrail:** refuses (unless `--force`) while work is mid-way: open tasks, a pending question, etc.
  - **Lossless but cheap:** if the session was compacted, a script filters *only the compacted-away
    part* of the transcript (keeping your answers, plan feedback, subagent results; clipping bulky
    tool output) and Haiku subagents mine it. No compaction means no mining, so zero extra cost.
  - With a tree, durable facts and decisions go into the tree and the handoff links to them.
- **`/pickup [id[:N]]`** continues from a handoff in a new session (a plain `/clear` never auto-loads).
- **`/ctx-clean`** is a better compact: handoff, then clear, then the handoff loads automatically
  into the fresh session. In the Desktop app Claude clears the session itself (you approve);
  in the CLI/VS Code you type `/clear` and the reload happens automatically. Opt-in
  `autoload_on_clear` (experimental) loads the latest open handoff on any `/clear`.
- **Safety net:** before any compaction, a filtered digest of the session is saved and the
  compacted session gets a one-line pointer to it.

### Advisor (on by default, everywhere)

- After a reply, when the context crosses **120K** (then **200K**) tokens, you see one line
  (to you only, zero tokens): the size, an estimate per request on API billing, and a suggestion
  to `/ctx-clean` or `/handoff` once the current piece of work is done. It's repeated every 10
  turns, and never shown while Claude is asking you something.
- Once per threshold, Claude also gets one line asking it to suggest switching *only* when a
  unit of work is finished.
- `/ctx-advisor off|on|status|threshold 150000 250000` (add `--global` for all sessions).
  Billing (API vs subscription) is detected by a script; on a subscription it shows tokens, not dollars.

## Install

Requirements: Claude Code, **Python 3.8+** on PATH (`python3`, `python` or `py -3`), and
`sh` (built into macOS/Linux; on Windows it comes with Git for Windows / Git Bash).

```
/plugin marketplace add 13sambhavjain/ctx-kit
/plugin install ctx-kit@sambhav-plugins
/plugin install read-guard@sambhav-plugins      # optional, independent
```

Then, in a project: `/ctx-kit:ctx init` (or just `/ctx init`).

Local development: `claude --plugin-dir ./plugins/ctx-kit`.

## Commands

| Command | What it does |
|---|---|
| `/ctx init` | Asks 4 questions once (storage, local-only, history, write mode), creates the skeleton |
| `/ctx update ["fact" @file …]` | Records durable learnings into the right nodes (review first) |
| `/ctx check` | Drift, renames, broken links, duplicates (zero tokens) |
| `/ctx audit` | Haiku checks drifted nodes against the code |
| `/ctx search <words>` | Keyword search over nodes (zero tokens) |
| `/ctx stamp <node>` | Mark a node as verified against the current code |
| `/handoff [slug] [--force] [--split]` | Writes a lossless handoff + next-session prompt(s) |
| `/pickup [id[:N]]` | Continues from a handoff |
| `/ctx-clean [slug]` | Handoff, then clear, then automatic reload |
| `/ctx-advisor on\|off\|status\|threshold` | Controls the session-size advisor |
| `/ctx-feedback [note]` | Drafts a GitHub issue with diagnostics only (no code, no paths) for **you** to submit |

## Where things live

| What | Default | Options |
|---|---|---|
| Config | `.claude/ctx-kit.json` | global defaults in `~/.ctx-kit/config.json` |
| Tree | `.claude/ctx/` | `.ctx/` (no permission prompts), or `~/.ctx-kit/trees/<repo>` (shared by all worktrees) |
| Rules | `.claude/rules/ctx-*.md` | turn off with `ctx config set rules false` |
| Handoffs | `.claude/handoffs/` | asked once per workspace whether they stay local-only |
| Session state (edit log, advisor, pending reloads) | plugin data folder | never inside your repo |

**Local-only** (the default) adds those paths to `.git/info/exclude` (your personal ignore
file, never committed) and puts the pointer in `CLAUDE.local.md`. Choose "committed" to
share the tree with your team through git.

**Worktrees:** local-only files aren't copied into new git worktrees, so with
workspace storage each worktree would get its own tree. If you use worktrees, choose
**home** storage. `init` warns you when it sees more than one worktree.

**Permissions:** writes into `.claude/` need approval in Claude Code. In the default
`script` write mode, the first time Claude runs the ctx command, choose
**"Yes, and don't ask again"**. After that, tree writes don't prompt. In `tool` mode you
approve "allow .claude edits for this session" once per session instead.

## Roadmap

- Better retrieval than keyword search; sharing a tree with a team.
- PowerShell launcher for Windows machines without Git Bash.

## Privacy

Everything stays on your machine. ctx-kit makes no network calls. `/ctx-feedback` only
prints a link; you decide whether to open and submit it.

## Development

```
python3 -m unittest discover -s tests      # standard library only
claude plugin validate --strict ./plugins/ctx-kit
claude plugin validate --strict .
```

Bump `version` in `plugins/ctx-kit/.claude-plugin/plugin.json` on every release; installed
copies only update when it changes.

## License

MIT

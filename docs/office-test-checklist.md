# ctx-kit test checklist (v0.1, context tree)

Write down **observations only**: what you did, what you saw, any error text. Don't copy
code or project names. When you're done, run `/ctx-feedback <summary>` to draft an issue.

## 0. Before you start
- [ ] `python --version`, `python3 --version` and `py -3 --version`: which ones work? (On Windows, `python3` may open the Microsoft Store; that's expected, the plugin skips it.)
- [ ] Is Git for Windows / Git Bash installed? (`git --version`). Hooks need `sh`.
- [ ] Which surface are you using: Desktop app Code tab, VS Code, or terminal `claude`? What version?

## 1. Install
- [ ] `/plugin marketplace add OWNER/REPO`. Did it work? If your company restricts plugin marketplaces, note the exact error.
- [ ] `/plugin install ctx-kit@sambhav-plugins`. Were you asked for "Python command"? Leave it empty first.
- [ ] Restart or start a new session in a test project. Did you see the one-time note "ctx-kit: no context tree here yet…"?

## 2. Init (use a small, non-sensitive project first)
- [ ] `/ctx init`. Did it ask 4 questions in one go? Pick **workspace**, **local-only yes**, **history no**, **script** mode.
- [ ] When asked to approve the `sh ".../run.sh" …` command, choose **"Yes, and don't ask again"**. Note the exact wording of the prompt.
- [ ] Check the files: `.claude/ctx/INDEX.md`, `.claude/ctx/modules/*.md`, `CLAUDE.local.md`, `.claude/rules/ctx-*.md`, and the end of `.git/info/exclude`.
- [ ] `git status`: the ctx files should **not** show up.

## 3. Lazy loading (the main token saving)
- [ ] Start a new session, run `/context`. Are any `.claude/ctx/*` nodes listed? (They shouldn't be. `CLAUDE.local.md` should be.)
- [ ] Ask Claude something about one module. Did it open `INDEX.md` and then only the relevant node?
- [ ] Open or edit a file under a module that has a rule. Did Claude mention reading the matching ctx node?

## 4. Writing
- [ ] `/ctx update "<a real fact about the code>" @<file>`. Did it show a review table first? Did the write prompt for permission again? (It shouldn't after "don't ask again".)
- [ ] Look at the node it changed: is the fact in the right place, without duplication?

## 5. Drift and renames (zero tokens)
- [ ] Add `sources: [<file>#<function>]` to a node (or let `/ctx update` do it), then `/ctx stamp <node>`.
- [ ] Change that function's body. `/ctx check` should report DRIFT.
- [ ] Rename the function without changing its body. `/ctx check` should report RENAMED, and `--fix` should update the node.
- [ ] Ask Claude to read that node. Did it get the "may be stale" warning?

## 6. Feedback loop
- [ ] `/ctx-feedback first impressions…`. Check the draft has **no paths, code or project names**, then open the link and submit it (or paste the saved draft into a new issue).

## Known gaps in v0.1
- Hooks run through `sh`. On Windows without Git Bash they won't run (write down what happens).
- Handoff, `/ctx-clean` and the advisor are not in this version yet.

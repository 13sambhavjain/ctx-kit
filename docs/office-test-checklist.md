# ctx-kit test checklist (v0.2)

Write down **observations only**: what you did, what you saw, any error text. Don't copy
code or project names. When you're done, run `/ctx-feedback <summary>` to draft an issue.

## 0. Before you start
- [ ] `python --version`, `python3 --version` and `py -3 --version`: which ones work? (On Windows, `python3` may open the Microsoft Store; that's expected, the plugin skips it.)
- [ ] Is Git for Windows / Git Bash installed? (`git --version`). Hooks need `sh`.
- [ ] Which surface are you using: Desktop app Code tab, VS Code, or terminal `claude`? What version?

## 1. Install
- [ ] `/plugin marketplace add 13sambhavjain/ctx-kit`. Did it work? If your company restricts plugin marketplaces, note the exact error.
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

## 6. Handoff
- [ ] In any project, `/handoff` while a task is clearly unfinished. Did it refuse and explain? Then `/handoff --force`.
- [ ] First time only: did it ask whether handoffs stay local-only?
- [ ] Open `.claude/handoffs/<id>/HANDOFF.md`. Is anything important from the session missing?
- [ ] New session: `/pickup`. Did it list or load the handoff and continue sensibly?
- [ ] `/ctx-clean` near the end of a task. **Desktop:** were you asked to approve clearing, and did the new session start with the handoff loaded? **CLI/VS Code:** after typing `/clear`, was it loaded?
- [ ] A plain `/clear` (without `/ctx-clean`) must **not** load anything.

## 7. Advisor
- [ ] `/ctx-advisor threshold 20000 40000`, then work a bit. Did a one-line note appear under a reply (not mid-work)? Where did it render in your app?
- [ ] `/ctx-advisor off` silences it. `/ctx-advisor status` shows billing as api or subscription. Is that right for your office setup?
- [ ] Reset with `/ctx-advisor threshold 120000 200000`.

## 8. read-guard (separate plugin)
- [ ] `/plugin install read-guard@sambhav-plugins`, then start a new session in a **coding** project.
- [ ] Ask Claude to read a file, then (later, file unchanged) to read it again. Did the second read show "[read-guard] Unchanged since your earlier Read…"? If it showed the full file, the replacement isn't accepted on your version, so note the Claude Code version.
- [ ] Let Claude edit a file, then ask it to read the whole file again. Did it get a diff instead of the full file?
- [ ] Did Claude ever get confused by a stub or diff (re-reading in a loop, or "can't see the file")? Note it.
- [ ] After a normal day of coding: `/read-guard stats`. Copy the numbers into feedback; they decide whether Bash de-dup is worth building.

## 9. Feedback loop
- [ ] `/ctx-feedback first impressions…`. Check the draft has **no paths, code or project names**, then open the link and submit it (or paste the saved draft into a new issue).

## Known gaps in v0.1
- Hooks run through `sh`. On Windows without Git Bash they won't run (write down what happens).
- If your Claude Code session compacts, check `.claude/handoffs/.checkpoints/` for a digest. Note whether `/handoff` afterwards said `CHUNKS` (mining ran).

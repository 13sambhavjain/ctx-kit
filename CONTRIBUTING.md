# Contributing / reporting

- **Easiest:** run `/ctx-feedback <what happened>` inside Claude Code. It drafts an issue with
  plugin diagnostics only (no code, file contents, project names or paths) and gives you a
  link to submit it yourself.
- Or open an issue with one of the templates: Observation, Bug, Idea.
- Please never paste proprietary code or company details into issues.
- PRs: keep the Python standard-library-only and Python 3.8 compatible; run
  `python3 -m unittest discover -s tests` and `claude plugin validate --strict` on both
  `.` and `./plugins/ctx-kit`.

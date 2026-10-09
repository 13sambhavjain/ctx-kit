"""ctx-kit command line. Invoked as: sh <plugin>/scripts/run.sh <command> ...

Commands
  status [--brief]                     config + tree summary (always exits 0)
  init --storage S --local-only y|n --nested-git y|n [--write-mode script|tool]
       [--review ask|auto] [--no-rules] [--overview TEXT]
  w <write|append|replace|mv|rm> <path> [path2] [--overwrite]   (content on stdin)
  index                                regenerate INDEX + rules
  check [--fix] [--json] [node ...]    drift / renames / links / duplicates (zero tokens)
  stamp <node ...>                     mark nodes verified against current code
  search <words ...>                   keyword search over nodes
  touched [--session ID]               files changed by a session (from hooks)
  commit [-m MSG]                      commit the tree's nested git repo (if enabled)
  config [get KEY | set KEY VALUE]
  feedback [--note TEXT] [--kind observation|bug|idea]
  handoff check-setup | setup --local-only y|n | readiness --session ID | mine --session ID [--all]
          | new --slug S --session ID | done <id> --session ID | list [--open] | show <id[:N]>
          | consume <id> | pending <id> --session ID
  advisor on|off|status [--session ID] [--global] | threshold SOFT [FIRM]
  hook <session-start|session-end|pre-compact|post-read|post-edit|stop|prompt>
"""
import argparse
import json
import os
import sys

from . import advisor, check, config, feedback, handoff, hooks, tree, util, writer


def yn(v):
    return str(v).lower() in ("y", "yes", "true", "1", "on")


def cmd_status(ws, a):
    if not ws.initialized:
        print("ctx-kit: not initialized in %s. Run `/ctx-kit:ctx init`." % ws.root)
        return 0
    nodes = tree.list_nodes(ws.ctx_root)
    print("ctx-kit status")
    print("  workspace : %s" % ws.root)
    print("  tree      : %s  (%d nodes)" % (tree.node_link_from_root(ws, ""), len(nodes)))
    print("  storage=%s local_only=%s nested_git=%s write_mode=%s review=%s rules=%s" % tuple(
        ws.get(k) for k in ("storage", "local_only", "nested_git", "write_mode", "review", "rules")))
    if not a.brief:
        lines, counts = check.run_check(ws)
        print("  check     : %s" % (", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "clean"))
    return 0


def cmd_init(ws, a):
    for line in tree.init(ws, a.storage, yn(a.local_only), yn(a.nested_git), a.write_mode, a.review,
                          rules=not a.no_rules, overview=a.overview):
        print(line)
    return 0


def cmd_w(ws, a):
    if a.handoffs and not ws.configured:
        print("ctx-kit: run `handoff setup` first", file=sys.stderr)
        return 2
    if not a.handoffs and not ws.initialized:
        print("ctx-kit: not initialized here; run init first", file=sys.stderr)
        return 2
    root = ws.handoffs_dir if a.handoffs else ws.ctx_root
    try:
        msg = writer.run(root, a.op, a.paths, writer.read_stdin() if a.op in ("write", "append", "replace") else "",
                         overwrite=a.overwrite)
    except writer.WriteError as e:
        print("ctxw: ERROR: %s" % e, file=sys.stderr)
        return 1
    print(msg)
    if not a.handoffs and not a.no_index:
        tree.write_index(ws)
        tree.write_rules(ws)
    return 0


def cmd_index(ws, a):
    print(util.rel(tree.write_index(ws), ws.root))
    for r in tree.write_rules(ws):
        print("rule: .claude/rules/%s" % r)
    return 0


def cmd_check(ws, a):
    lines, counts = check.run_check(ws, fix=a.fix, only=a.nodes or None)
    if a.json:
        print(json.dumps({"issues": lines, "counts": counts}, indent=2))
    else:
        print("\n".join(lines) if lines else "clean: no drift, renames, broken links or duplicates")
        if counts:
            print("-- " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    return 0


def cmd_stamp(ws, a):
    for line in check.stamp(ws, a.nodes):
        print(line)
    return 0


def cmd_search(ws, a):
    res = check.search(ws, " ".join(a.words), limit=a.limit)
    if not res:
        print("no matches")
    for score, r, summ, hit in res:
        print("%s — %s%s" % (tree.node_link_from_root(ws, r), summ, ("\n    > " + hit) if hit else ""))
    return 0


def cmd_touched(ws, a):
    d = os.path.join(config.plugin_data_dir(a.data), "sessions")
    sids = [a.session] if a.session else (sorted(os.listdir(d), key=lambda s: os.path.getmtime(os.path.join(d, s)))[-1:] if os.path.isdir(d) else [])
    files = []
    for sid in sids:
        p = os.path.join(d, sid, "touched.txt")
        if os.path.exists(p):
            for line in util.read_text(p).splitlines():
                f = line.split("\t", 1)[-1]
                if f not in files:
                    files.append(f)
    print("\n".join(files) if files else "(no recorded edits)")
    return 0


def cmd_commit(ws, a):
    print(tree.commit(ws, a.message or "ctx update"))
    return 0


def cmd_config(ws, a):
    if a.action == "get" or not a.action:
        print(json.dumps(ws.cfg if not a.key else ws.cfg.get(a.key), indent=2))
        return 0
    if a.action == "set":
        v = a.value
        if v in ("true", "false"):
            v = v == "true"
        elif v is not None and v.isdigit():
            v = int(v)
        ws.save({a.key: v})
        print("set %s = %r" % (a.key, v))
        return 0
    return 1


def cmd_feedback(ws, a):
    title, body, url, kinds = feedback.build(ws, a.data, a.note or "", a.kind)
    print("TITLE: " + title)
    print("----- DRAFT (review before sending) -----")
    print(body)
    print("-----")
    if kinds:
        print("redacted: " + ", ".join(kinds))
    if url:
        print("OPEN THIS LINK TO FILE IT YOURSELF:\n" + url)
    else:
        p = os.path.join(config.plugin_data_dir(a.data), "feedback-%s.md" % util.now_iso().replace(":", ""))
        util.atomic_write(p, "# " + title + "\n\n" + body + "\n")
        print("Saved draft to %s (paste it into a new GitHub issue)." % p)
    return 0


def cmd_handoff(ws, a):
    data = config.plugin_data_dir(a.data)
    act = a.action
    if act == "check-setup":
        print(handoff.ensure_setup(ws) or "OK")
    elif act == "setup":
        print(handoff.setup(ws, yn(a.local_only)))
    elif act == "readiness":
        print(json.dumps(handoff.readiness(ws, a.session, a.transcript), indent=2))
    elif act == "mine":
        status, paths = handoff.mine(ws, data, a.session, a.transcript, force_all=a.all)
        print(status)
        for p in paths:
            print(p)
    elif act == "new":
        if not ws.configured:
            print("FIRST_HANDOFF_IN_WORKSPACE (run `handoff setup --local-only y|n` first)", file=sys.stderr)
            return 2
        folder, meta = handoff.new(ws, a.slug, a.session)
        print("id: %s" % meta["id"])
        print("folder: %s" % util.rel(folder, ws.root))
        print("write files with: w --handoffs write %s/HANDOFF.md (and %s/next-1-<part>.prompt.md)" % (meta["id"], meta["id"]))
    elif act == "done":
        handoff.mark_mined(ws, data, a.session, a.transcript)
        h, prompt, _ = handoff.resolve(ws, a.ident)
        if h and not h["prompts"]:
            print("WARNING: %s has no next-*.prompt.md yet" % h["id"])
        print("recorded: next handoff in this session starts after this point")
    elif act == "list":
        hs = handoff.list_handoffs(ws, only_open=a.open)
        if not hs:
            print("(no handoffs)")
        for h in hs:
            print("%s  [%s]  prompts: %s" % (h["id"], h["status"], ", ".join(h["prompts"]) or "-"))
    elif act == "show":
        h, prompt, cands = handoff.resolve(ws, a.ident)
        if not h:
            print("not found or ambiguous: %s" % ", ".join(c["id"] for c in cands) if cands else "not found")
            return 1
        print("id: %s  status: %s" % (h["id"], h["status"]))
        print("handoff: %s" % util.rel(os.path.join(h["folder"], "HANDOFF.md"), ws.root))
        if prompt:
            print("prompt: %s\n-----" % util.rel(os.path.join(h["folder"], prompt), ws.root))
            print(util.read_text(os.path.join(h["folder"], prompt)))
    elif act == "consume":
        r = handoff.set_status(ws, a.ident, "consumed")
        print("consumed %s" % r if r else "not found")
    elif act == "pending":
        r = handoff.set_pending(ws, data, a.ident, a.session)
        print("pending %s for the next /clear of this session" % r if r else "not found (needs a prompt file)")
    return 0


def cmd_advisor(ws, a):
    data = config.plugin_data_dir(a.data)
    if a.action in ("on", "off"):
        flag = a.action == "on"
        if a.glob or not a.session:
            g = util.load_json(config.GLOBAL_CONFIG, {}) or {}
            g["advisor"] = dict(g.get("advisor") or {}, enabled=flag)
            util.save_json(config.GLOBAL_CONFIG, g)
            print("advisor %s (global default)" % a.action)
        else:
            advisor.set_session(data, a.session, flag)
            print("advisor %s for this session" % a.action)
    elif a.action == "threshold":
        g = util.load_json(config.GLOBAL_CONFIG, {}) or {}
        adv = dict(g.get("advisor") or {})
        adv["soft_tokens"] = int(a.values[0])
        adv["firm_tokens"] = int(a.values[1]) if len(a.values) > 1 else max(int(a.values[0]) + 80000, int(a.values[0]))
        g["advisor"] = adv
        util.save_json(config.GLOBAL_CONFIG, g)
        print("advisor thresholds: soft=%d firm=%d tokens" % (adv["soft_tokens"], adv["firm_tokens"]))
    else:
        st = advisor.load_state(data, a.session) if a.session else {}
        adv = ws.get("advisor") or {}
        print("advisor: %s (session override: %s)" % ("on" if adv.get("enabled", True) else "off",
                                                       st.get("enabled", "none")))
        print("thresholds: soft=%s firm=%s, repeat every %s turns" % (adv.get("soft_tokens"), adv.get("firm_tokens"), adv.get("every_turns")))
        if st.get("tokens"):
            print("last seen context: ~%dK tokens" % round(st["tokens"] / 1000.0))
        print("billing: %s" % advisor.billing(ws))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="ctx", description="ctx-kit")
    p.add_argument("--root", help="workspace root (default: auto)")
    p.add_argument("--data", help="plugin data dir (default: $CLAUDE_PLUGIN_DATA or ~/.ctx-kit/data)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("status"); s.add_argument("--brief", action="store_true")
    s = sub.add_parser("init")
    s.add_argument("--storage", choices=["workspace", "dotctx", "home"], default="workspace")
    s.add_argument("--local-only", default="y")
    s.add_argument("--nested-git", default="n")
    s.add_argument("--write-mode", choices=["script", "tool"], default="script")
    s.add_argument("--review", choices=["ask", "auto"], default="ask")
    s.add_argument("--no-rules", action="store_true")
    s.add_argument("--overview")
    s = sub.add_parser("w")
    s.add_argument("op", choices=["write", "append", "replace", "mv", "rm"])
    s.add_argument("paths", nargs="+")
    s.add_argument("--overwrite", action="store_true")
    s.add_argument("--handoffs", action="store_true", help="target the handoffs folder instead of the tree")
    s.add_argument("--no-index", action="store_true")
    sub.add_parser("index")
    s = sub.add_parser("check"); s.add_argument("--fix", action="store_true"); s.add_argument("--json", action="store_true"); s.add_argument("nodes", nargs="*")
    s = sub.add_parser("stamp"); s.add_argument("nodes", nargs="+")
    s = sub.add_parser("search"); s.add_argument("words", nargs="+"); s.add_argument("--limit", type=int, default=8)
    s = sub.add_parser("touched"); s.add_argument("--session")
    s = sub.add_parser("commit"); s.add_argument("-m", "--message")
    s = sub.add_parser("config"); s.add_argument("action", nargs="?", choices=["get", "set"]); s.add_argument("key", nargs="?"); s.add_argument("value", nargs="?")
    s = sub.add_parser("feedback"); s.add_argument("--note"); s.add_argument("--kind", default="observation", choices=["observation", "bug", "idea"])
    s = sub.add_parser("handoff")
    s.add_argument("action", choices=["check-setup", "setup", "readiness", "mine", "new", "done", "list", "show", "consume", "pending"])
    s.add_argument("ident", nargs="?", default="")
    s.add_argument("--session", default="")
    s.add_argument("--transcript")
    s.add_argument("--slug", default="session")
    s.add_argument("--local-only", default="y")
    s.add_argument("--all", action="store_true")
    s.add_argument("--open", action="store_true")
    s = sub.add_parser("advisor")
    s.add_argument("action", nargs="?", default="status", choices=["on", "off", "status", "threshold"])
    s.add_argument("values", nargs="*")
    s.add_argument("--session", default="")
    s.add_argument("--global", dest="glob", action="store_true")
    s = sub.add_parser("hook"); s.add_argument("event")
    return p


CMDS = {"status": cmd_status, "init": cmd_init, "w": cmd_w, "index": cmd_index, "check": cmd_check,
        "stamp": cmd_stamp, "search": cmd_search, "touched": cmd_touched, "commit": cmd_commit,
        "config": cmd_config, "feedback": cmd_feedback, "handoff": cmd_handoff, "advisor": cmd_advisor}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:  # keep Windows consoles from choking on non-ASCII output
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # allow --root/--data anywhere on the line (skills append them at the end)
    pre = []
    rest = list(argv)
    for flag in ("--root", "--data"):
        while flag in rest:
            i = rest.index(flag)
            if i + 1 < len(rest):
                pre += [flag, rest[i + 1]]
                del rest[i:i + 2]
            else:
                del rest[i]
    a = build_parser().parse_args(pre + rest)
    if a.cmd == "hook":
        return hooks.main(a.event, a.data)
    if not a.cmd:
        print(__doc__)
        return 0
    ws = config.Workspace(a.root)
    if not ws.initialized and a.cmd not in ("status", "init", "config", "feedback", "handoff", "advisor", "w"):
        print("ctx-kit: not initialized in %s; run `/ctx-kit:ctx init` first." % ws.root, file=sys.stderr)
        return 2
    try:
        return CMDS[a.cmd](ws, a)
    except Exception as e:
        util.log_error(config.plugin_data_dir(a.data), "cli " + a.cmd)
        print("ctx-kit: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 0 if a.cmd == "status" else 1

#!/usr/bin/env python3
"""Fake `codex plugin ...` for tests (see _fake.py)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fake as F  # noqa: E402

TOOL = "codex"
argv = sys.argv[1:]
F.log(TOOL, argv)
print('WARNING: proceeding, even though we could not create PATH aliases: fake', file=sys.stderr)   # the real CLI does this
home = os.environ.get("CODEX_HOME")
if home and not os.path.isdir(home):
    sys.exit(f"Error: failed to resolve CODEX_HOME\n  CODEX_HOME points to {home!r}, but that path does not exist")
if argv[:1] != ["plugin"]:
    sys.exit("fake codex: only `plugin` is supported")
argv = argv[1:]
st = F.load(TOOL)


def fail(msg):
    print("Error: " + msg, file=sys.stderr)
    sys.exit(1)


if argv[:2] == ["marketplace", "list"]:
    rows = []
    for name, m in st["marketplaces"].items():
        rows.append({"name": name, "root": m["src"],
                     "marketplaceSource": {"sourceType": m["kind"], "source": m["src"] if m["kind"] == "local" else F.canon_remote(m["src"])}})
    print(json.dumps({"marketplaces": rows}))
elif argv[:2] == ["marketplace", "add"]:
    pos, opts = F.parse(argv[2:])
    src = pos[0]
    name = F.name_of(TOOL, src)
    if name is None:
        fail("invalid marketplace source format; expected owner/repo, a git URL, or a local marketplace path")
    if F.should_fail(TOOL, "marketplace-add", name):
        fail("injected failure")
    kind = "local" if F.is_local(src) else "git"
    cur = st["marketplaces"].get(name)
    if cur and (cur["src"], cur.get("ref")) != (src, opts.get("ref")):
        fail("marketplace '%s' is already added from a different source; remove it before adding this source" % name)
    already = bool(cur)
    st["marketplaces"][name] = {"kind": kind, "src": src, "ref": opts.get("ref")}
    F.save(TOOL, st)
    print(json.dumps({"marketplaceName": name, "installedRoot": src, "alreadyAdded": already}))
elif argv[:2] == ["marketplace", "remove"]:
    name = argv[2]
    if name not in st["marketplaces"]:
        fail("marketplace '%s' is not configured" % name)
    if F.should_fail(TOOL, "marketplace-remove", name):
        fail("injected failure")
    del st["marketplaces"][name]
    F.drop_marketplace_plugins(st, name)
    F.save(TOOL, st)
    print(json.dumps({"marketplaceName": name, "installedRoot": None}))
elif argv[:1] == ["list"]:
    inst = [{"pluginId": pid, "name": pid.split("@")[0], "marketplaceName": pid.split("@")[1], "version": p["version"],
             "installed": True, "enabled": p["enabled"]} for pid, p in st["plugins"].items()]
    print(json.dumps({"installed": inst, "available": []}))
elif argv[:1] == ["add"]:
    pid = argv[1]
    mp = pid.split("@")[-1]
    if mp not in st["marketplaces"]:
        fail("plugin `%s` was not found in marketplace `%s`" % (pid.split("@")[0], mp))
    if F.should_fail(TOOL, "install", pid):
        fail("injected failure")
    st["plugins"].setdefault(pid, {"version": "1.0.0", "enabled": True})
    F.save(TOOL, st)
    print(json.dumps({"pluginId": pid, "name": pid.split("@")[0], "marketplaceName": mp, "version": "1.0.0"}))
elif argv[:1] == ["remove"]:
    pid = argv[1]
    if F.should_fail(TOOL, "uninstall", pid):
        fail("injected failure")
    st["plugins"].pop(pid, None)          # removing something that is not installed still succeeds
    F.save(TOOL, st)
    print(json.dumps({"pluginId": pid, "name": pid.split("@")[0], "marketplaceName": pid.split("@")[-1]}))
else:
    sys.exit("fake codex: unsupported command: " + " ".join(argv))
